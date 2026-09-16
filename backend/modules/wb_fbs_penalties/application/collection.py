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
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Перечитать детализацию отчёта реализации и оставить строки с удержаниями.

    Окно перекрывает несколько недель: строки отчёта появляются с задержкой и
    дописываются задним числом, а повтор по `rrd_id` ничего не задваивает.
    Первый сбор кабинета берёт глубже — за штрафами она приходит спустя недели.
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
        tracked = await self.penalties.tracked(seller_id)
        depth = self.window_days if tracked is not None and tracked.collected_at is not None else self.backfill_days
        date_to = stamp.date()
        date_from = date_to - timedelta(days=depth)
        # Сеть идёт до первой записи: лимит метода — запрос в минуту, и держать
        # транзакцию всё это время незачем.
        await self.session.commit()

        rows = await self.client.rows(str(seller_id), date_from, date_to)
        charged = [row for row in rows if row.charged]

        if not await self.penalties.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(date_from, date_to, len(rows), 0, skipped=True)
        await self.penalties.upsert_rows(seller_id, charged, now=stamp)
        await self.penalties.finish_collection(seller_id, now=stamp)
        await self.session.commit()
        return CollectionResult(date_from, date_to, len(rows), len(charged))
