from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    ChatEvent,
)
from backend.modules.wb_review_chats.domain import (
    GROUP_BARE,
    GROUP_FOLLOWED,
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


def dialogs(*events: ChatEvent, now: datetime = LATER):
    return build_dialogs(list(events), since=SINCE, until=UNTIL, window=WINDOW, now=now)


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


def test_a_prompt_without_our_message_is_the_baseline() -> None:
    answered, quiet = dialogs(
        event("a", 0, "prompt"),
        event("a", 5, "client"),
        event("b", -10, "prompt"),
        # Менеджер из кабинета — не наша рассылка.
        event("b", -5, "manager", "Здравствуйте! Мы готовы предложить"),
    )

    assert (answered.group, answered.outcome) == (GROUP_BARE, OUTCOME_REPLIED)
    assert (quiet.group, quiet.outcome, quiet.follow_up_at) == (GROUP_BARE, OUTCOME_SILENT, None)


def test_a_buyer_who_answered_before_our_message_stays_in_the_baseline() -> None:
    """В день запуска рассылка прошла по старым диалогам: на уже данный ответ она не влияла."""
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 20, "client"), event("a", 600, "ours"))

    assert (dialog.group, dialog.outcome, dialog.follow_up_late) == (GROUP_BARE, OUTCOME_REPLIED, True)
    assert dialog.anchor_at == START


def test_a_late_message_is_not_a_reply_to_ours() -> None:
    """Чат у покупателя один: вопрос про другой заказ через неделю — не ответ."""
    [dialog] = dialogs(event("a", 0, "prompt"), event("a", 1, "ours"), event("a", 60 * 24 * 4, "client"))

    assert (dialog.outcome, dialog.reply_at) == (OUTCOME_SILENT, None)


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


def test_percentages_leave_out_the_dialogs_still_waiting() -> None:
    found = dialogs(
        event("a", 0, "prompt"),
        event("a", 1, "ours"),
        event("a", 2, "client"),
        event("b", 0, "prompt"),
        event("b", 1, "ours"),
        event("c", 0, "prompt"),
        now=START + timedelta(hours=1),
    )
    followed, bare = summarize(found, GROUP_FOLLOWED), summarize(found, GROUP_BARE)

    assert (followed.total, followed.replied, followed.silent, followed.pending) == (2, 1, 0, 1)
    assert followed.reply_rate == 1.0
    assert (bare.total, bare.pending, bare.reply_rate) == (1, 1, None)
