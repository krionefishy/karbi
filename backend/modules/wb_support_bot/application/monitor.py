"""Мониторинг бота поддержки внутри админки: снимок данных, проверки, запись истории.

Воркер раз в проход записывает результат (`record`), страница читает и пересчитывает его при
открытии (`view`). Уведомлений наружу нет: проблема видна плашкой в админке.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_support_bot.application.reply_stats import (
    SendRow,
    SliceRow,
    continue_stats,
    health,
    late_dialogs,
    open_escalations,
)
from backend.modules.wb_support_bot.domain.health import (
    Check,
    Severity,
    Snapshot,
    Thresholds,
    evaluate,
    worst,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    MonitorEventModel,
    ReplyVerdictModel,
    SupportBotRepository,
)
from backend.shared.kafka_streams.topics import SupportBotTopics
from backend.shared.outbox.repository import OutboxRepository

MSK = ZoneInfo("Europe/Moscow")
HISTORY_DAYS = 7
DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class MonitorDay:
    date: str
    warn_minutes: int
    crit_minutes: int


@dataclass(frozen=True, slots=True)
class MonitorView:
    checked_at: datetime
    worker_checked_at: datetime | None
    overall: Severity
    checks: list[Check]
    incidents: list[MonitorEventModel]
    days: list[MonitorDay]


def _slice_row(row: ReplyVerdictModel) -> SliceRow:
    return SliceRow(
        chat_id=row.chat_id,
        prompt_at=row.prompt_at,
        first_event_at=row.first_event_at,
        last_event_at=row.last_event_at,
        status=row.status,
        category=row.category,
        guard=row.guard,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        model=row.model,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        error=row.error,
    )


def history_days(events: list[MonitorEventModel], now: datetime) -> list[MonitorDay]:
    """Минуты жёлтого и красного по дням (по Москве) за последние семь дней."""
    today = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    starts = [today - timedelta(days=offset) for offset in range(HISTORY_DAYS - 1, -1, -1)]
    result: list[MonitorDay] = []
    for start in starts:
        end = start + DAY
        warn = crit = 0.0
        for event in events:
            began = max(event.started_at, start)
            finished = min(event.ended_at or now, end)
            if finished <= began:
                continue
            minutes = (finished - began).total_seconds() / 60
            if event.severity == Severity.CRIT.value:
                crit += minutes
            else:
                warn += minutes
        result.append(MonitorDay(start.date().isoformat(), round(warn), round(crit)))
    return result


class BotMonitor:
    def __init__(
        self,
        session: AsyncSession,
        repository: SupportBotRepository,
        *,
        daily_cap: int,
        reply_gate_mode: str,
        reply_prompt_version: str,
        limits: Thresholds = Thresholds(),
    ) -> None:
        self.session = session
        self.repository = repository
        self.chats = ChatMirror(session)
        self.daily_cap = daily_cap
        self.reply_gate_mode = reply_gate_mode
        self.reply_prompt_version = reply_prompt_version
        self.limits = limits

    async def snapshot(
        self, seller_id: uuid.UUID, now: datetime, *, worker_checked_at: datetime | None, check_worker: bool
    ) -> Snapshot:
        repository, limits = self.repository, self.limits
        tracked = await repository.tracked(seller_id)
        mirror = await self.chats.state(seller_id)
        counts = await repository.send_status_counts_since(seller_id, now - DAY)
        # Сухие отправки до перехода на «всю рассылку» нарушением не считаются.
        live_since = await repository.live_since(seller_id)
        dry_run = counts.get("dry_run", 0)
        if live_since is not None and live_since > now - DAY:
            dry_run = (await repository.send_status_counts_since(seller_id, live_since)).get("dry_run", 0)
        midnight = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        slices_models = await repository.reply_slices(seller_id, self.reply_prompt_version, now - timedelta(days=14))
        rows = [_slice_row(row) for row in slices_models]
        states = await repository.dialog_states(seller_id, {(row.chat_id, row.prompt_at) for row in rows})
        day_rows = [row for row in rows if row.requested_at >= now - DAY and row.guard is None]
        asked = len(day_rows)
        bad = sum(
            1 for row in day_rows if row.status == "failed" or (row.status == "done" and row.category == "unclear")
        )
        auth = sum(
            1
            for row in day_rows
            if row.status == "failed" and any(code in (row.error or "") for code in ("401", "403"))
        )
        limited = sum(1 for row in day_rows if row.status == "failed" and "429" in (row.error or ""))
        stuck = sum(
            1
            for row in rows
            if row.guard is None
            and row.status != "done"
            and now - row.first_event_at > limits.reply_stuck
            and (state := states.get((row.chat_id, row.prompt_at))) is not None
            and state.reply_hold
        )
        week = [row for row in rows if row.last_event_at >= now - timedelta(days=7)]
        sends_week = [
            SendRow(chat, prompt, moment)
            for chat, prompt, moment in await repository.sent_rows(seller_id, now - timedelta(days=7))
        ]
        stop_dialogs, late = late_dialogs(week, sends_week, limits.late_tolerance)
        overdue = [
            row
            for row in open_escalations(rows, {key: (model.state, model.closed) for key, model in states.items()})
            if now - row.last_event_at > limits.escalation_wait
        ]
        continues = continue_stats(rows)
        asked_health = health(day_rows, now)
        outbox = OutboxRepository(self.session)
        return Snapshot(
            now=now,
            worker_checked_at=worker_checked_at,
            check_worker=check_worker,
            mode=tracked.mode if tracked else "dry_run",
            paused_reason=tracked.paused_reason if tracked else None,
            globally_paused=await repository.is_paused(),
            mirror_read_through=mirror.read_through if mirror else None,
            mirror_error=mirror.error if mirror else None,
            sends_failed_24h=counts.get("failed", 0),
            sends_unknown_24h=counts.get("unknown", 0),
            unknown_stuck=await repository.unknown_stuck_count(seller_id, now - limits.unknown_stuck),
            dry_run_24h=dry_run,
            sent_today=await repository.sent_today(seller_id, midnight),
            daily_cap=self.daily_cap,
            active_templates=len(await repository.templates(seller_id, active_only=True)),
            verdicts_pending_stuck=await repository.verdicts_pending_stuck(seller_id, now - limits.verdict_stuck),
            reply_gate_mode=self.reply_gate_mode,
            reply_stuck=stuck,
            reply_asked_24h=asked,
            reply_bad_24h=bad,
            model_auth_errors_24h=auth,
            model_rate_limited_24h=limited,
            median_decision_minutes=asked_health.median_decision_minutes,
            calls_24h=asked_health.calls,
            outbox_stuck=await outbox.count_stuck(
                (SupportBotTopics.CLASSIFY_REQUESTED, SupportBotTopics.REPLY_REQUESTED), now - limits.outbox_stuck_age
            ),
            stop_dialogs=stop_dialogs,
            late_dialogs=late,
            open_escalations_overdue=len(overdue),
            continue_dialogs=continues.dialogs,
            escalated_after_continue=continues.escalated_after,
            max_continues=continues.max_per_dialog,
        )

    async def record(self, seller_id: uuid.UUID, now: datetime) -> Severity:
        """Вызывается воркером каждый проход: сохраняет снимок и обновляет историю проблем."""
        snapshot = await self.snapshot(seller_id, now, worker_checked_at=None, check_worker=False)
        checks = evaluate(snapshot, self.limits)
        overall = worst([check.severity for check in checks])
        await self.repository.save_monitor_state(
            seller_id,
            now,
            overall.value,
            [{"id": check.id, "severity": check.severity.value, "value": check.value} for check in checks],
        )
        current = {check.id: check for check in checks if check.severity in (Severity.WARN, Severity.CRIT)}
        for event in await self.repository.open_monitor_events(seller_id):
            check = current.get(event.check_id)
            if check is None or check.severity.value != event.severity:
                event.ended_at = now
            else:
                current.pop(event.check_id)
        for check in current.values():
            self.repository.add_monitor_event(
                MonitorEventModel(
                    seller_id=seller_id,
                    check_id=check.id,
                    title=check.title,
                    severity=check.severity.value,
                    value=check.value,
                    started_at=now,
                )
            )
        await self.session.commit()
        return overall

    async def view(self, seller_id: uuid.UUID, now: datetime | None = None) -> MonitorView:
        moment = now or datetime.now(UTC)
        state = await self.repository.monitor_state(seller_id)
        worker_at = state.checked_at if state else None
        # Воркер ещё не записывал проходов: проверка живости получит «нет данных», а не ложную тревогу.
        snapshot = await self.snapshot(seller_id, moment, worker_checked_at=worker_at, check_worker=True)
        checks = evaluate(snapshot, self.limits)
        events = await self.repository.monitor_events_since(seller_id, moment - timedelta(days=HISTORY_DAYS))
        return MonitorView(
            checked_at=moment,
            worker_checked_at=worker_at,
            overall=worst([check.severity for check in checks]),
            checks=checks,
            incidents=events,
            days=history_days(events, moment),
        )

    async def overall(self, seller_id: uuid.UUID) -> Severity:
        """Общий уровень по последней записи воркера, без пересчёта: для плашки и списка автоматизаций."""
        state = await self.repository.monitor_state(seller_id)
        if state is None:
            return Severity.NODATA
        return Severity(state.overall)
