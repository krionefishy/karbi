import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBFinanceClient


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

        more = await self.penalties.pending_report_count(seller_id) > 0
        if not more:
            await self.penalties.finish_collection(seller_id, now=stamp)
        await self.session.commit()
        return CollectionResult(len(headers), loaded, rows_seen, rows_kept, more=more)
