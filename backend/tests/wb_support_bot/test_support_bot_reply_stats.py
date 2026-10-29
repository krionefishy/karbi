import uuid
from datetime import UTC, datetime, timedelta

from backend.modules.wb_support_bot.application.reply_stats import (
    EffectRow,
    SendRow,
    SliceRow,
    arm_stats,
    build_summary,
    continue_stats,
    daily_points,
    health,
    late_sends,
    open_escalations,
    worst_per_dialog,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
PROMPT = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


def effect(chat: str, *, hours_ago: float, live: bool, replied_after_hours: float | None) -> EffectRow:
    first = NOW - timedelta(hours=hours_ago)
    reply = first + timedelta(hours=replied_after_hours) if replied_after_hours is not None else None
    return EffectRow(chat, PROMPT, first, live, reply)


def piece(
    chat: str,
    minutes: int,
    category: str | None,
    *,
    status: str = "done",
    guard: str | None = None,
    completed_after: int | None = 12,
    tokens: int = 800,
) -> SliceRow:
    first = PROMPT + timedelta(days=1, minutes=minutes)
    return SliceRow(
        chat_id=chat,
        prompt_at=PROMPT,
        first_event_at=first,
        last_event_at=first,
        status=status,
        category=category,
        guard=guard,
        requested_at=first,
        completed_at=first + timedelta(minutes=completed_after) if completed_after is not None else None,
        model="qwen" if guard is None and status == "done" else None,
        tokens_in=tokens if guard is None and status == "done" else None,
        tokens_out=30 if guard is None and status == "done" else None,
    )


def test_the_share_that_replied_counts_only_dialogs_old_enough() -> None:
    rows = [
        effect("a", hours_ago=60, live=True, replied_after_hours=3),
        effect("b", hours_ago=60, live=True, replied_after_hours=40),
        effect("c", hours_ago=60, live=True, replied_after_hours=None),
        effect("d", hours_ago=30, live=True, replied_after_hours=2),
        effect("e", hours_ago=10, live=True, replied_after_hours=1),
    ]

    stats = arm_stats(rows, NOW)

    assert (stats.dialogs, stats.eligible24, stats.replied24) == (5, 4, 2)
    assert (stats.eligible48, stats.replied48) == (3, 2)


def test_arms_are_counted_apart_and_the_day_is_moscow_time() -> None:
    first = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)  # 26.10 01:30 по Москве
    rows = [
        EffectRow("a", PROMPT, first, True, first + timedelta(hours=2)),
        EffectRow("b", PROMPT, first, False, None),
    ]

    points = daily_points(rows, NOW, NOW - timedelta(days=14))

    assert [point.date for point in points] == ["2026-10-26"]
    point = points[0]
    assert (point.started_live, point.started_control) == (1, 1)
    assert (point.replied48_live, point.replied48_control) == (1, 0)
    assert (point.replies_live, point.replies_control) == (1, 0)


def test_a_dialog_gets_its_heaviest_class() -> None:
    rows = [
        piece("a", 0, "refuses"),
        piece("a", 10, "threat"),
        piece("b", 0, "thanks"),
        piece("c", 0, None, guard="stopword"),
    ]

    assert sorted(worst_per_dialog(rows)) == ["stopword", "thanks", "threat"]


def test_a_send_after_a_stop_reply_is_late_and_after_a_continue_reply_is_not() -> None:
    stop = piece("a", 0, "threat")
    keep = piece("b", 0, "refuses")
    sends = [
        SendRow("a", PROMPT, stop.first_event_at + timedelta(minutes=12)),
        SendRow("a", PROMPT, stop.first_event_at - timedelta(minutes=30)),
        SendRow("b", PROMPT, keep.first_event_at + timedelta(minutes=12)),
    ]

    result = late_sends([stop, keep], sends)

    assert (result.count, result.total_sends, result.median_lag_minutes, result.max_lag_minutes) == (1, 3, 12, 12)


def test_continues_are_counted_per_dialog_with_what_came_after() -> None:
    rows = [
        piece("a", 0, "refuses"),
        piece("a", 100, "refuses"),
        piece("a", 200, "threat"),
        piece("b", 0, "noise"),
        piece("b", 100, "thanks"),
        piece("c", 0, "thanks"),
    ]

    stats = continue_stats(rows)

    assert (stats.dialogs, stats.escalated_after, stats.resolved_after, stats.max_per_dialog) == (2, 1, 1, 2)
    assert stats.distribution == [(1, 1), (2, 1)]


def test_health_counts_pending_stuck_and_unclear() -> None:
    rows = [
        piece("a", 0, "refuses", completed_after=20),
        piece("b", 0, "unclear", completed_after=40),
        piece("c", 0, None, status="pending", completed_after=None),
        piece("d", 0, None, status="failed", completed_after=None),
        piece("e", 0, None, guard="stopword", completed_after=0),
    ]

    result = health(rows, PROMPT + timedelta(days=1, hours=3))

    assert (result.total, result.pending, result.stuck, result.failed, result.unclear) == (5, 1, 2, 1, 1)
    assert result.median_decision_minutes == 30 and result.tokens_in == 1600 and result.calls == 2


def test_an_escalation_is_open_until_a_manager_answers() -> None:
    rows = [
        piece("a", 0, "threat"),
        piece("b", 0, "stop_request"),
        piece("c", 0, "refuses"),
        piece("d", 0, None, guard="stopword"),
    ]
    states = {
        ("a", PROMPT): ("awaiting_us", False),
        ("b", PROMPT): ("awaiting_buyer", False),
        ("c", PROMPT): ("awaiting_us", False),
        ("d", PROMPT): ("awaiting_us", True),
    }

    found = open_escalations(rows, states)

    assert [row.chat_id for row in found] == ["a"]


def test_the_summary_collects_everything_for_one_period() -> None:
    summary = build_summary(
        days=14,
        now=NOW,
        effect=[
            effect("a", hours_ago=60, live=True, replied_after_hours=3),
            effect("b", hours_ago=60, live=False, replied_after_hours=None),
        ],
        slices=[piece("a", 0, "refuses"), piece("a", 100, None, status="pending", completed_after=None)],
        sends=[],
        dialogs={("a", PROMPT): ("in_campaign", False)},
        live_since=PROMPT + timedelta(days=1, minutes=50),
    )

    assert summary.effect_live.replied48 == 1 and summary.effect_control.replied48 == 0
    assert summary.actions == {"continue": 1, "stop": 0, "wait": 1}
    assert summary.categories == [("refuses", 1)]
    assert summary.categories_before == [("refuses", 1)] and summary.categories_after == [("undetermined", 1)]
    assert summary.escalations_open == 0 and uuid.uuid4()
