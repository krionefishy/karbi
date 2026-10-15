import random
from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    ChatEvent,
)
from backend.modules.wb_support_bot.domain import (
    CampaignRules,
    DialogState,
    Pace,
    SendWindow,
    SentMessage,
    match,
    next_send_at,
    project_dialog,
)

START = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)  # 09:00 МСК
RULES = CampaignRules(silence=timedelta(hours=4), duration=timedelta(days=5), max_campaigns=1)


def event(minutes: float, kind: str, text: str = "") -> ChatEvent:
    sender, source, prompt = {
        "prompt": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, True),
        "third_party": (CHAT_SENDER_SELLER, CHAT_SOURCE_API, False),
        "specialist": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, False),
        "bot": (CHAT_SENDER_SELLER, CHAT_SOURCE_API, False),
        "buyer": (CHAT_SENDER_CLIENT, "ios", False),
    }[kind]
    return ChatEvent(
        event_id=f"{kind}-{minutes}",
        chat_id="1:chat",
        sender=sender,
        source=source,
        added_at=START + timedelta(minutes=minutes),
        is_new_chat=False,
        review_prompt=prompt,
        nm_id=None,
        rid=None,
        text=text or None,
        has_attachments=False,
    )


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def project(events, now_minutes, **kwargs):
    return project_dialog(events, kwargs.pop("sent", []), now=at(now_minutes), rules=RULES, **kwargs)


# --- состояния -------------------------------------------------------------


def test_no_reply_when_buyer_silent_even_after_third_party_message():
    events = [event(0, "prompt"), event(1, "third_party", "Сообщение выше автоматическое")]
    result = project(events, now_minutes=60 * 48)
    assert result.state is DialogState.NO_REPLY
    assert result.start_campaign is False


def test_awaiting_us_when_buyer_wrote_and_nobody_answered():
    events = [event(0, "prompt"), event(1, "third_party"), event(30, "buyer", ".")]
    assert project(events, now_minutes=60 * 24).state is DialogState.AWAITING_US


def test_third_party_message_is_not_our_reply():
    # сообщение сторонней рассылки пришло раньше ответа покупателя и не считается ответом
    events = [event(0, "prompt"), event(1, "third_party"), event(30, "buyer", ".")]
    assert project(events, now_minutes=600).our_last_at is None


def test_awaiting_buyer_until_silence_passes():
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "Опишите проблему")]
    result = project(events, now_minutes=60 + 3 * 60)
    assert result.state is DialogState.AWAITING_BUYER
    assert result.start_campaign is False


def test_campaign_starts_after_silence_following_specialist_reply():
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "Опишите проблему")]
    result = project(events, now_minutes=60 + 4 * 60)
    assert result.state is DialogState.IN_CAMPAIGN
    assert result.start_campaign is True
    assert result.human_last_at == at(60)


def test_buyer_wrote_again_after_our_reply_returns_to_specialist():
    events = [
        event(0, "prompt"),
        event(30, "buyer", "."),
        event(60, "specialist", "Опишите проблему"),
        event(120, "buyer", "всё плохо"),
    ]
    assert project(events, now_minutes=60 * 24).state is DialogState.AWAITING_US


def test_campaign_continues_with_bot_messages_in_feed():
    sent = [SentMessage(at(300), "Добрый день! Как дела с товаром?")]
    events = [
        event(0, "prompt"),
        event(30, "buyer", "."),
        event(60, "specialist", "Опишите проблему"),
        event(301, "bot", "Добрый день! Как дела с товаром?"),
    ]
    result = project(events, now_minutes=600, sent=sent, campaign_started_at=at(300), campaigns_count=1)
    assert result.state is DialogState.IN_CAMPAIGN
    assert result.start_campaign is False
    assert result.human_last_at == at(60)  # сообщение бота человеческим не считается


def test_buyer_reply_ends_campaign():
    events = [
        event(0, "prompt"),
        event(30, "buyer", "."),
        event(60, "specialist", "Опишите проблему"),
        event(301, "bot", "напоминание"),
        event(400, "buyer", "ладно, расскажу"),
    ]
    sent = [SentMessage(at(300), "напоминание")]
    result = project(events, now_minutes=500, sent=sent, campaign_started_at=at(300), campaigns_count=1)
    assert result.state is DialogState.AWAITING_US
    assert result.end_campaign is True


def test_specialist_message_during_campaign_pauses_it():
    events = [
        event(0, "prompt"),
        event(30, "buyer", "."),
        event(60, "specialist", "Опишите проблему"),
        event(301, "bot", "напоминание"),
        event(350, "specialist", "Позвоните нам"),
    ]
    sent = [SentMessage(at(300), "напоминание")]
    result = project(events, now_minutes=360, sent=sent, campaign_started_at=at(300), campaigns_count=1)
    assert result.state is DialogState.AWAITING_BUYER
    assert result.end_campaign is True


def test_campaign_exhausted_after_duration():
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "Опишите проблему")]
    result = project(
        events,
        now_minutes=300 + 5 * 24 * 60,
        campaign_started_at=at(300),
        campaigns_count=1,
    )
    assert result.state is DialogState.EXHAUSTED


def test_no_second_campaign_when_limit_reached():
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "Опишите проблему")]
    result = project(events, now_minutes=60 + 10 * 60, campaigns_count=1)
    assert result.state is DialogState.AWAITING_BUYER
    assert result.start_campaign is False


def test_second_campaign_allowed_when_limit_raised():
    rules = CampaignRules(silence=timedelta(hours=4), duration=timedelta(days=5), max_campaigns=2)
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "ответ")]
    result = project_dialog(events, [], now=at(60 + 5 * 60), rules=rules, campaigns_count=1)
    assert result.state is DialogState.IN_CAMPAIGN
    assert result.start_campaign is True


# --- стоп-слова ------------------------------------------------------------


def test_stopwords_no_contact_and_attention():
    assert match("Не пишите мне больше!").no_contact
    assert match("ХВАТИТ").no_contact
    assert match("Достали уже").no_contact
    assert match("Отстаньте").no_contact
    assert match("Добавьте в чёрный список").no_contact
    assert match("Я пожалуюсь на вас").attention
    assert match("это спам").attention


def test_stopwords_word_boundaries_and_neutral_text():
    assert not match("судя по всему, товар сломан").no_contact
    assert not match("возврат денег, замена, брак").no_contact
    assert not match("возврат денег, замена, брак").attention
    assert not match(None).no_contact
    assert not match("Здравствуйте, у меня проблема с пылесосом").attention


def test_stop_flags_land_in_projection():
    events = [event(0, "prompt"), event(30, "buyer", "Не пишите мне"), event(40, "buyer", "это спам")]
    result = project(events, now_minutes=600)
    assert result.no_contact and result.attention


# --- расписание ------------------------------------------------------------


def seeded() -> random.Random:
    return random.Random(7)


def test_next_send_within_window_keeps_hour_plus_jitter():
    window, pace = SendWindow(), Pace()
    last = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)  # 10:00 МСК
    moment = next_send_at(last, window=window, pace=pace, rng=seeded())
    assert last + timedelta(hours=1, minutes=1) <= moment <= last + timedelta(hours=1, minutes=10)


def test_next_send_after_window_moves_to_next_morning():
    window, pace = SendWindow(), Pace()
    last = datetime(2026, 10, 10, 18, 30, tzinfo=UTC)  # 21:30 МСК, шаг упрётся в 22:30
    moment = next_send_at(last, window=window, pace=pace, rng=seeded())
    opening = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)  # 09:00 МСК следующего дня
    assert opening <= moment <= opening + timedelta(minutes=10)


def test_next_send_before_window_moves_to_same_morning():
    window, pace = SendWindow(), Pace()
    last = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)  # 04:00 МСК
    moment = next_send_at(last, window=window, pace=pace, rng=seeded())
    opening = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    assert opening <= moment <= opening + timedelta(minutes=10)


def test_window_boundaries():
    window = SendWindow()
    assert window.contains(datetime(2026, 10, 10, 6, 0, tzinfo=UTC))  # 09:00
    assert not window.contains(datetime(2026, 10, 10, 5, 59, tzinfo=UTC))  # 08:59
    assert window.contains(datetime(2026, 10, 10, 18, 59, tzinfo=UTC))  # 21:59
    assert not window.contains(datetime(2026, 10, 10, 19, 0, tzinfo=UTC))  # 22:00


def test_chat_link_uses_the_uuid_without_the_feed_prefix():
    from backend.modules.wb_support_bot.domain import DEFAULT_CHAT_URL_TEMPLATE, chat_url

    assert (
        chat_url(DEFAULT_CHAT_URL_TEMPLATE, "1:a9a572e8-13dc-b219-872d-431684d4544a")
        == "https://seller.wildberries.ru/chat-with-clients?chatId=a9a572e8-13dc-b219-872d-431684d4544a"
    )
    assert chat_url("", "1:abc") is None


def test_canary_membership_is_stable_and_roughly_the_requested_share():
    from backend.modules.wb_support_bot.domain import canary_bucket, in_canary

    chats = [f"1:chat-{number}" for number in range(2000)]
    first = [in_canary(chat, PROMPT, 10) for chat in chats]
    again = [in_canary(chat, PROMPT, 10) for chat in chats]

    assert first == again
    assert 140 <= sum(first) <= 260  # около 10 % из 2000
    assert all(0 <= canary_bucket(chat, PROMPT) < 100 for chat in chats)
    # при росте процента прежние диалоги остаются в канарейке
    wider = [in_canary(chat, PROMPT, 30) for chat in chats]
    assert all(wide for narrow, wide in zip(first, wider, strict=True) if narrow)
    assert not in_canary("1:x", PROMPT, 0)
    assert in_canary("1:x", PROMPT, 100)


def test_a_campaign_does_not_start_for_a_conversation_that_ended_long_ago():
    rules = CampaignRules(silence=timedelta(hours=4), duration=timedelta(days=5), max_idle=timedelta(days=2))
    events = [event(0, "prompt"), event(30, "buyer", "."), event(60, "specialist", "Опишите проблему")]

    recent = project_dialog(events, [], now=at(60 + 30 * 60), rules=rules)
    stale = project_dialog(events, [], now=at(60 + 5 * 24 * 60), rules=rules)

    assert recent.state is DialogState.IN_CAMPAIGN and recent.start_campaign is True
    assert stale.state is DialogState.AWAITING_BUYER and stale.start_campaign is False


PROMPT = START
