import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBRealizationClient


@dataclass(frozen=True, slots=True)
class CollectionResult:
    date_from: date
    date_to: date
    seen: int
    kept: int
    # Догрузка ещё не закончена: кабинет остаётся в очереди на следующий проход.
    more: bool = False
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Перечитать детализацию отчёта реализации и оставить строки с удержаниями.

    Один запрос за проход: лимит метода у токенов селлеров — запрос в час, и
    вторая страница подряд получила бы 429. Первичная догрузка идёт по
    странице за проход с курсором на кабинете; пока она не закончена, отметка
    сбора не ставится и кабинет остаётся в очереди. Обычное окно перекрывает
    две недели: строки отчёта появляются с задержкой и дописываются задним
    числом, а повтор по `rrd_id` ничего не задваивает.
    """

    def __init__(
        self,
        session: AsyncSession,
        penalties: PenaltiesRepository,
        client: WBRealizationClient,
        *,
        window_days: int,
        backfill_days: int,
    ) -> None:
        self.session = session
        self.penalties = penalties
        self.client = client
        self.window_days = window_days
        self.backfill_days = backfill_days

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        stamp = now or datetime.now(UTC)
        today = stamp.date()
        tracked = await self.penalties.tracked(seller_id)
        backfilling = tracked is not None and not tracked.backfill_done
        if backfilling and tracked is not None and tracked.backfill_from is None:
            await self.penalties.start_backfill(seller_id, today - timedelta(days=self.backfill_days), today)
            tracked = await self.penalties.tracked(seller_id)
        if backfilling and tracked is not None and tracked.backfill_from and tracked.backfill_to:
            date_from, date_to, cursor = tracked.backfill_from, tracked.backfill_to, tracked.backfill_cursor
        else:
            date_from, date_to, cursor = today - timedelta(days=self.window_days), today, 0
        # Сеть идёт до первой записи: держать транзакцию весь запрос незачем.
        await self.session.commit()

        page = await self.client.page(str(seller_id), date_from, date_to, cursor=cursor)
        charged = [row for row in page.rows if row.charged]

        if not await self.penalties.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(date_from, date_to, len(page.rows), 0, skipped=True)
        await self.penalties.upsert_rows(seller_id, charged, now=stamp)
        more = False
        if backfilling:
            if page.exhausted:
                await self.penalties.finish_backfill(seller_id)
            else:
                await self.penalties.advance_backfill(seller_id, page.cursor)
                more = True
        if not more:
            await self.penalties.finish_collection(seller_id, now=stamp)
        await self.session.commit()
        return CollectionResult(date_from, date_to, len(page.rows), len(charged), more=more)
