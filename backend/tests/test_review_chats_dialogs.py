from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    ChatEvent,
)
from backend.modules.wb_review_chats.domain import (
    GROUP_BEFORE,
    GROUP_EARLY,
    GROUP_FOLLOWED,
    GROUP_MISSED,
    OUTCOME_PENDING,
    OUTCOME_REPLIED,
    OUTCOME_SILENT,
    build_dialogs,
    summarize,
)

START = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
SINCE, UNTIL = START - timedelta(hours=9), START + timedelta(hours=15)
WINDOW = timedelta(hours=48)
LATER = START + timedelta(days=5)


def event(chat: str, minutes: float, kind: str, text: str = "") -> ChatEvent:
    sender, source = {
        "prompt": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL),
        "ours": (CHAT_SENDER_SELLER, CHAT_SOURCE_API),
        "manager": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL),
        "client": (CHAT_SENDER_CLIENT, "ios"),
    }[kind]
    return ChatEvent(
        event_id=f"{chat}-{minutes}",
        chat_id=chat,
        sender=sender,
        source=source,
        added_at=START + timedelta(minutes=minutes),
        is_new_chat=kind == "prompt",
        review_prompt=kind == "prompt",
        nm_id=1304195061 if kind == "prompt" else None,
        rid=None,
        text=text or kind,
        has_attachments=False,
    )


# Рассылка запущена до начала периода; `launch_at=None` — не запускалась вовсе.
LAUNCH = SINCE


def dialogs(*events: ChatEvent, now: datetime = LATER, launch_at: datetime | None = LAUNCH):
    return build_dialogs(list(events), since=SINCE, until=UNTIL, window=WINDOW, now=now, launch_at=launch_at)


def test_a_reply_after_our_message_counts_and_silence_counts_once_the_window_is_over() -> None:
    answered, quiet = dialogs(
        event("a", 0, "prompt"),
        event("a", 1, "ours"),
        event("a", 30, "client", "Товар сломался"),
        event("b", -10, "prompt"),
        event("b", -9, "ours"),
    )

    assert (answered.group, answered.outcome) == (GROUP_FOLLOWED, OUTCOME_REPLIED)
    assert answered.reply_text == "Товар сломался"
    assert answered.anchor_at == START + timedelta(minutes=1)
    assert (quiet.group, quiet.outcome, quiet.reply_at) == (GROUP_FOLLOWED, OUTCOME_SILENT, None)


def test_silence_inside_the_window_is_not_a_verdict_yet() -> None:
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 1, "ours"), now=START + timedelta(hours=3))

    assert dialog.outcome == OUTCOME_PENDING


def test_a_prompt_without_our_message_after_launch_is_an_early_reply_or_a_miss() -> None:
    """Программа не шлёт сообщение, если покупатель уже написал; молчание без нашего сообщения — пропуск."""
    answered, quiet = dialogs(
        event("a", 0, "prompt"),
        event("a", 5, "client"),
        event("b", -10, "prompt"),
        # Менеджер из кабинета — не наша рассылка.
        event("b", -5, "manager", "Здравствуйте! Мы готовы предложить"),
    )

    assert (answered.group, answered.outcome) == (GROUP_EARLY, OUTCOME_REPLIED)
    assert (quiet.group, quiet.outcome, quiet.follow_up_at) == (GROUP_MISSED, OUTCOME_SILENT, None)


def test_before_the_launch_every_dialog_is_the_baseline() -> None:
    launch = START + timedelta(hours=5)
    answered, quiet = dialogs(
        event("a", 0, "prompt"),
        event("a", 5, "client"),
        event("b", -10, "prompt"),
        launch_at=launch,
    )
    never = dialogs(event("c", 0, "prompt"), launch_at=None)
    # Диалог, открывший рассылку: его автосообщение WB раньше момента запуска на секунды.
    [first] = dialogs(event("d", 299, "prompt"), event("d", 300, "ours"), launch_at=launch)

    assert (answered.group, quiet.group, never[0].group) == (GROUP_BEFORE, GROUP_BEFORE, GROUP_BEFORE)
    assert first.group == GROUP_FOLLOWED
    assert (answered.outcome, quiet.outcome) == (OUTCOME_REPLIED, OUTCOME_SILENT)


def test_a_buyer_who_answered_before_our_message_is_an_early_reply() -> None:
    """Покупатель успел написать за минуту до нашего сообщения: на его ответ оно не влияло."""
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 1, "client"), event("a", 2, "ours"))

    assert (dialog.group, dialog.outcome, dialog.follow_up_late) == (GROUP_EARLY, OUTCOME_REPLIED, True)
    assert dialog.anchor_at == START


def test_a_late_message_is_not_a_reply_to_ours() -> None:
    """Чат у покупателя один: вопрос про другой заказ через неделю — не ответ."""
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 1, "ours"), event("a", 60 * 24 * 4, "client"))

    assert (dialog.outcome, dialog.reply_at) == (OUTCOME_SILENT, None)


def test_a_manager_answering_through_an_api_client_hours_later_is_not_our_message() -> None:
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 300, "ours", "Заявка одобрена"))

    assert (dialog.group, dialog.follow_up_at) == (GROUP_MISSED, None)


def test_a_second_prompt_in_the_same_chat_starts_a_new_dialog() -> None:
    second, first = dialogs(
        event("a", -60, "client", "Где мой заказ?"),
        event("a", 0, "prompt"),
        event("a", 1, "ours"),
        event("a", 10, "client"),
        event("a", 300, "prompt"),
        event("a", 301, "ours"),
    )

    assert (first.outcome, second.outcome) == (OUTCOME_REPLIED, OUTCOME_SILENT)
    assert second.prompt_at == START + timedelta(minutes=300)


def test_only_prompts_inside_the_period_are_reported() -> None:
    assert dialogs(event("a", -60 * 24, "prompt"), event("a", 5, "client")) == []


def test_percentages_are_shares_of_all_dialogs_so_a_fresh_day_is_not_a_hundred_percent() -> None:
    """Девять ответили, десять ещё ждём — это 47 %, а не 100 % «от известных исходов»."""
    found = dialogs(
        event("a", 0, "prompt"),
        event("a", 1, "ours"),
        event("a", 2, "client"),
        event("b", 0, "prompt"),
        event("b", 1, "ours"),
        event("c", 0, "prompt"),
        now=START + timedelta(hours=1),
    )
    followed, missed, everything = summarize(found, GROUP_FOLLOWED), summarize(found, GROUP_MISSED), summarize(found)

    assert (followed.total, followed.replied, followed.silent, followed.pending) == (2, 1, 0, 1)
    assert (followed.reply_rate, followed.silent_rate, followed.pending_rate) == (0.5, 0.0, 0.5)
    assert (missed.total, missed.pending, missed.reply_rate) == (1, 1, 0.0)
    assert (everything.total, everything.reply_rate) == (3, 1 / 3)
    assert summarize([], GROUP_FOLLOWED).reply_rate is None
