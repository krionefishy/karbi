import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import OrderMirror
from backend.modules.wb_fbs_penalties.application.tracing import row_keys, row_trace
from backend.modules.wb_fbs_penalties.domain import NO_ORDER_TRACE, ReportRow, RowTrace
from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBFinanceClient

# Досводка несведённых строк: столько дней назад зеркало ещё могло застать задание,
# и не чаще раза в столько часов — чтобы не гонять зеркало по одним и тем же
# отменённым заказам каждый проход.
RETRACE_DAYS = 45
RETRACE_HOURS = 6
TRACE_CHUNK = 5000


@dataclass(frozen=True, slots=True)
class CollectionResult:
    reports_seen: int
    reports_loaded: int
    rows_seen: int
    rows_kept: int
    # Отчёты ещё остались: кабинет остаётся в очереди на следующий проход.
    more: bool = False
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Список отчётов реализации и детализация тех, что ещё не прочитаны.

    Отчёт после формирования не меняется, поэтому читается один раз и
    отмечается. За проход берётся не больше `reports_per_run` отчётов: у метода
    лимит запрос в минуту, и первичная догрузка крупного кабинета иначе
    заняла бы полчаса без heartbeat. Пока отчёты остаются, отметка сбора не
    ставится и кабинет остаётся в очереди.

    Окно списка — `window_days` назад (первый сбор — `backfill_days`). Отчёты
    суточные: за день D отчёт появляется на D+1, поэтому воркер спрашивает список
    каждые несколько часов, а не раз в сутки. Отчёт, целиком лежащий в уже
    прочитанном (суточный внутри собранного недельного), не перечитывается.
    """

    def __init__(
        self,
        session: AsyncSession,
        penalties: PenaltiesRepository,
        client: WBFinanceClient,
        *,
        window_days: int,
        backfill_days: int,
        reports_per_run: int,
    ) -> None:
        self.session = session
        self.penalties = penalties
        self.client = client
        self.orders = OrderMirror(session)
        self.window_days = window_days
        self.backfill_days = backfill_days
        self.reports_per_run = reports_per_run

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        stamp = now or datetime.now(UTC)
        today = stamp.date()
        seller_key = str(seller_id)
        tracked = await self.penalties.tracked(seller_id)
        depth = self.window_days if tracked is not None and tracked.collected_at is not None else self.backfill_days
        # Сеть идёт до первой записи: держать транзакцию весь запрос незачем.
        await self.session.commit()

        headers = await self.client.reports(seller_key, today - timedelta(days=depth), today)
        if not await self.penalties.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(len(headers), 0, 0, 0, skipped=True)
        await self.penalties.upsert_reports(seller_id, headers, now=stamp)
        await self.penalties.finish_covered_reports(seller_id, now=stamp)
        await self.session.commit()

        loaded = rows_seen = rows_kept = 0
        for report in await self.penalties.pending_reports(seller_id, limit=self.reports_per_run):
            cursor = report.cursor
            while True:
                page = await self.client.page(seller_key, report.report_id, cursor=cursor)
                charged = [row for row in page.rows if row.charged]
                rows_seen += len(page.rows)
                rows_kept += len(charged)
                if not await self.penalties.still_tracked(seller_id):
                    await self.session.rollback()
                    return CollectionResult(len(headers), loaded, rows_seen, rows_kept, skipped=True)
                await self.penalties.upsert_rows(seller_id, charged, now=stamp)
                await self._trace(seller_id, charged, now=stamp)
                if page.exhausted:
                    await self.penalties.finish_report(seller_id, report.report_id, now=stamp)
                    loaded += 1
                else:
                    await self.penalties.advance_report(seller_id, report.report_id, page.cursor)
                # Страница — отдельная транзакция: упавший на третьей странице
                # процесс не должен перечитывать первые две.
                await self.session.commit()
                if page.exhausted:
                    break
                cursor = page.cursor

        if not await self.retrace(seller_id, now=stamp):
            return CollectionResult(len(headers), loaded, rows_seen, rows_kept, skipped=True)
        more = await self.penalties.pending_report_count(seller_id) > 0
        if not more:
            await self.penalties.finish_collection(seller_id, now=stamp)
        await self.session.commit()
        return CollectionResult(len(headers), loaded, rows_seen, rows_kept, more=more)

    async def retrace(self, seller_id: uuid.UUID, *, now: datetime) -> bool:
        """Досвести строки без задания: зеркало снимает список раз в час и могло
        застать заказ уже после того, как строка легла в отчёт. False — кабинет отключили."""
        while True:
            rows = await self.penalties.rows_to_trace(
                seller_id,
                since=now.date() - timedelta(days=RETRACE_DAYS),
                stale_before=now - timedelta(hours=RETRACE_HOURS),
                limit=TRACE_CHUNK,
            )
            if not rows:
                return True
            if not await self.penalties.still_tracked(seller_id):
                await self.session.rollback()
                return False
            await self._trace(seller_id, rows, now=now)
            await self.session.commit()

    async def _trace(self, seller_id: uuid.UUID, rows: Sequence[ReportRow], *, now: datetime) -> None:
        if not rows:
            return
        traces = await self.orders.resolve(
            seller_id,
            rids=[row.srid for row in rows if row.srid],
            order_ids=[row.assembly_id for row in rows if row.assembly_id],
            sticker_ids=[row.sticker_id for row in rows if row.sticker_id],
        )
        found: dict[int, RowTrace] = {}
        for row in rows:
            trace = next((traces[key] for key in row_keys(row) if key in traces), None)
            found[row.rrd_id] = row_trace(trace) if trace else NO_ORDER_TRACE
        await self.penalties.set_traces(seller_id, found, now=now)
