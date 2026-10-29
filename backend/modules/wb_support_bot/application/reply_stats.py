"""Статистика ответов покупателей на рассылку: чистые функции над строками из базы.

Сюда не заходят ни сеть, ни сессия, поэтому всё считается и проверяется на готовых списках.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from backend.modules.wb_support_bot.domain import (
    CONTINUE_CATEGORIES,
    ESCALATION_CATEGORIES,
    GUARD_ATTACHMENT,
    GUARD_STOPWORD,
    Gate,
    gate_decision,
)

BUCKET_UNDETERMINED = "undetermined"
ESCALATION_BUCKETS = frozenset({*ESCALATION_CATEGORIES, GUARD_STOPWORD})
RESOLVED_BUCKETS = frozenset({"says_deleted", "thanks"})
# Чем раньше класс в списке, тем он «хуже» для диалога: диалог в сводке получает самый тяжёлый класс среди срезов.
SEVERITY_ORDER = (
    "threat",
    GUARD_STOPWORD,
    "stop_request",
    GUARD_ATTACHMENT,
    "cant_delete",
    "not_helped",
    "refuses",
    "waiting_seller",
    "sent_details",
    "question",
    "says_deleted",
    "thanks",
    "noise",
    "unclear",
    BUCKET_UNDETERMINED,
)
STUCK_AFTER = timedelta(minutes=60)
LATE_WINDOW = timedelta(hours=24)
MSK = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True, slots=True)
class EffectRow:
    """Диалог, по которому бот отправлял (живое или сухое сообщение): первая отправка и первый ответ после неё."""

    chat_id: str
    prompt_at: datetime
    first_at: datetime
    live: bool
    reply_first_at: datetime | None


@dataclass(frozen=True, slots=True)
class SliceRow:
    """Срез ответов покупателя с классом: то, что нужно статистике от строки `reply_verdicts`."""

    chat_id: str
    prompt_at: datetime
    first_event_at: datetime
    last_event_at: datetime
    status: str
    category: str | None
    guard: str | None
    requested_at: datetime
    completed_at: datetime | None
    model: str | None
    tokens_in: int | None
    tokens_out: int | None

    @property
    def bucket(self) -> str:
        if self.guard is not None:
            return self.guard
        if self.status == "done":
            return self.category or "unclear"
        return BUCKET_UNDETERMINED

    @property
    def gate(self) -> Gate:
        return gate_decision(guard=self.guard, status=self.status, category=self.category)


@dataclass(frozen=True, slots=True)
class SendRow:
    chat_id: str
    prompt_at: datetime
    sent_at: datetime


@dataclass(frozen=True, slots=True)
class ArmStats:
    dialogs: int = 0
    eligible24: int = 0
    replied24: int = 0
    eligible48: int = 0
    replied48: int = 0


@dataclass(frozen=True, slots=True)
class DailyPoint:
    date: str
    started_live: int = 0
    started_control: int = 0
    replied48_live: int = 0
    replied48_control: int = 0
    replies_live: int = 0
    replies_control: int = 0


@dataclass(frozen=True, slots=True)
class LateSends:
    count: int
    total_sends: int
    median_lag_minutes: int | None
    max_lag_minutes: int | None


@dataclass(frozen=True, slots=True)
class ContinueStats:
    dialogs: int
    escalated_after: int
    resolved_after: int
    max_per_dialog: int
    distribution: list[tuple[int, int]]


@dataclass(frozen=True, slots=True)
class Health:
    total: int
    pending: int
    stuck: int
    failed: int
    unclear: int
    median_decision_minutes: int | None
    tokens_in: int
    tokens_out: int
    calls: int


@dataclass(slots=True)
class ReplySummary:
    days: int
    effect_live: ArmStats
    effect_control: ArmStats
    categories: list[tuple[str, int]] = field(default_factory=list)
    categories_slices: list[tuple[str, int]] = field(default_factory=list)
    categories_before: list[tuple[str, int]] = field(default_factory=list)
    categories_after: list[tuple[str, int]] = field(default_factory=list)
    actions: dict[str, int] = field(default_factory=dict)
    daily: list[DailyPoint] = field(default_factory=list)
    escalations_open: int = 0
    late_sends: LateSends = LateSends(0, 0, None, None)
    continues: ContinueStats = ContinueStats(0, 0, 0, 0, [])
    health: Health = Health(0, 0, 0, 0, 0, None, 0, 0, 0)


def arm_stats(rows: list[EffectRow], now: datetime) -> ArmStats:
    eligible24 = [row for row in rows if row.first_at <= now - timedelta(hours=24)]
    eligible48 = [row for row in rows if row.first_at <= now - timedelta(hours=48)]

    def replied(subset: list[EffectRow], within: timedelta) -> int:
        return sum(1 for row in subset if row.reply_first_at and row.reply_first_at - row.first_at <= within)

    return ArmStats(
        dialogs=len(rows),
        eligible24=len(eligible24),
        replied24=replied(eligible24, timedelta(hours=24)),
        eligible48=len(eligible48),
        replied48=replied(eligible48, timedelta(hours=48)),
    )


def daily_points(rows: list[EffectRow], now: datetime, since: datetime) -> list[DailyPoint]:
    days: dict[str, dict[str, int]] = defaultdict(Counter)
    for row in rows:
        arm = "live" if row.live else "control"
        started = row.first_at.astimezone(MSK).date().isoformat()
        days[started][f"started_{arm}"] += 1
        if (
            row.first_at <= now - timedelta(hours=48)
            and row.reply_first_at
            and row.reply_first_at - row.first_at <= timedelta(hours=48)
        ):
            days[started][f"replied48_{arm}"] += 1
        if row.reply_first_at and row.reply_first_at >= since:
            days[row.reply_first_at.astimezone(MSK).date().isoformat()][f"replies_{arm}"] += 1
    return [DailyPoint(date=day, **dict(counts)) for day, counts in sorted(days.items())]


def _count(buckets: list[str]) -> list[tuple[str, int]]:
    counts = Counter(buckets)
    return sorted(counts.items(), key=lambda item: (-item[1], SEVERITY_ORDER.index(item[0])))


def worst_per_dialog(rows: list[SliceRow]) -> list[str]:
    by_dialog: dict[tuple[str, datetime], list[str]] = defaultdict(list)
    for row in rows:
        by_dialog[(row.chat_id, row.prompt_at)].append(row.bucket)
    return [min(buckets, key=SEVERITY_ORDER.index) for buckets in by_dialog.values()]


def _by_dialog(rows: list[SliceRow]) -> dict[tuple[str, datetime], list[SliceRow]]:
    grouped: dict[tuple[str, datetime], list[SliceRow]] = defaultdict(list)
    for row in sorted(rows, key=lambda item: item.first_event_at):
        grouped[(row.chat_id, row.prompt_at)].append(row)
    return grouped


def late_sends(rows: list[SliceRow], sends: list[SendRow]) -> LateSends:
    """Сообщения бота, ушедшие после ответа, по которому рассылка должна была остановиться."""
    stops: dict[tuple[str, datetime], list[SliceRow]] = defaultdict(list)
    for row in rows:
        if row.gate is Gate.STOP:
            stops[(row.chat_id, row.prompt_at)].append(row)
    lags: list[float] = []
    for send in sends:
        replies = [
            row.first_event_at
            for row in stops.get((send.chat_id, send.prompt_at), [])
            if row.first_event_at < send.sent_at <= row.first_event_at + LATE_WINDOW
        ]
        if replies:
            lags.append((send.sent_at - max(replies)).total_seconds() / 60)
    return LateSends(
        count=len(lags),
        total_sends=len(sends),
        median_lag_minutes=round(median(lags)) if lags else None,
        max_lag_minutes=round(max(lags)) if lags else None,
    )


def continue_stats(rows: list[SliceRow]) -> ContinueStats:
    per_dialog: Counter[int] = Counter()
    escalated = resolved = longest = 0
    dialogs = 0
    for slices in _by_dialog(rows).values():
        positions = [index for index, item in enumerate(slices) if item.gate is Gate.CONTINUE]
        if not positions:
            continue
        dialogs += 1
        longest = max(longest, len(positions))
        per_dialog[len(positions)] += 1
        later = [item.bucket for item in slices[positions[0] + 1 :]]
        escalated += 1 if any(bucket in ESCALATION_BUCKETS for bucket in later) else 0
        resolved += 1 if any(bucket in RESOLVED_BUCKETS for bucket in later) else 0
    return ContinueStats(dialogs, escalated, resolved, longest, sorted(per_dialog.items()))


def health(rows: list[SliceRow], now: datetime) -> Health:
    asked = [row for row in rows if row.guard is None]
    delays = [
        (row.completed_at - row.last_event_at).total_seconds() / 60
        for row in asked
        if row.status == "done" and row.completed_at is not None
    ]
    return Health(
        total=len(rows),
        pending=sum(1 for row in asked if row.status == "pending"),
        stuck=sum(1 for row in asked if row.status != "done" and now - row.first_event_at > STUCK_AFTER),
        failed=sum(1 for row in asked if row.status == "failed"),
        unclear=sum(1 for row in asked if row.status == "done" and row.category == "unclear"),
        median_decision_minutes=round(median(delays)) if delays else None,
        tokens_in=sum(row.tokens_in or 0 for row in asked),
        tokens_out=sum(row.tokens_out or 0 for row in asked),
        calls=sum(1 for row in asked if row.model is not None),
    )


def open_escalations(rows: list[SliceRow], dialogs: dict[tuple[str, datetime], tuple[str, bool]]) -> list[SliceRow]:
    """Последний срез диалога — угроза, просьба прекратить или стоп-слово, а менеджер ещё не ответил.

    `dialogs` — состояние и признак «закрыт» по ключу диалога. Менеджер ответил, когда диалог
    ушёл из `awaiting_us`.
    """
    found: list[SliceRow] = []
    for key, slices in _by_dialog(rows).items():
        last = slices[-1]
        state = dialogs.get(key)
        if last.bucket in ESCALATION_BUCKETS and state is not None and state[0] == "awaiting_us" and not state[1]:
            found.append(last)
    return sorted(found, key=lambda item: item.last_event_at, reverse=True)


def build_summary(
    *,
    days: int,
    now: datetime,
    effect: list[EffectRow],
    slices: list[SliceRow],
    sends: list[SendRow],
    dialogs: dict[tuple[str, datetime], tuple[str, bool]],
    live_since: datetime | None,
) -> ReplySummary:
    since = now - timedelta(days=days)
    summary = ReplySummary(
        days=days,
        effect_live=arm_stats([row for row in effect if row.live], now),
        effect_control=arm_stats([row for row in effect if not row.live], now),
    )
    summary.categories = _count(worst_per_dialog(slices))
    summary.categories_slices = _count([row.bucket for row in slices])
    if live_since is not None:
        summary.categories_before = _count(worst_per_dialog([r for r in slices if r.first_event_at < live_since]))
        summary.categories_after = _count(worst_per_dialog([r for r in slices if r.first_event_at >= live_since]))
    gates = Counter(row.gate.value for row in slices)
    summary.actions = {gate.value: gates.get(gate.value, 0) for gate in (Gate.CONTINUE, Gate.STOP, Gate.WAIT)}
    summary.daily = daily_points(effect, now, since)
    summary.escalations_open = len(open_escalations(slices, dialogs))
    summary.late_sends = late_sends(slices, sends)
    summary.continues = continue_stats(slices)
    summary.health = health(slices, now)
    return summary


__all__ = [
    "CONTINUE_CATEGORIES",
    "ArmStats",
    "ContinueStats",
    "DailyPoint",
    "EffectRow",
    "Health",
    "LateSends",
    "ReplySummary",
    "SendRow",
    "SliceRow",
    "build_summary",
    "open_escalations",
]
