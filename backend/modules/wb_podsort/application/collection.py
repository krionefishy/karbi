import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_podsort.domain import BarcodeInfo, DayCount, OrderLine
from backend.modules.wb_podsort.infrastructure.postgres import PodsortRepository
from backend.modules.wb_podsort.infrastructure.wb import WBPodsortStatisticsClient


@dataclass(frozen=True, slots=True)
class CollectionResult:
    days_loaded: int
    orders: int
    # Сутки, которые ещё ждут: кабинет остаётся в очереди на следующий проход.
    remaining: int
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


def summarize(lines: Sequence[OrderLine]) -> tuple[list[DayCount], list[BarcodeInfo]]:
    """Заказы суток → штуки на пару «баркод + регион» и справка по баркодам."""
    orders: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    infos: dict[str, BarcodeInfo] = {}
    for line in lines:
        counts = orders[(line.barcode, line.region)]
        counts[0] += 1
        counts[1] += int(line.fbs)
        infos[line.barcode] = BarcodeInfo(line.barcode, line.nm_id, line.vendor_code, line.subject, line.tech_size)
    summary = [DayCount(barcode, region, total, fbs) for (barcode, region), (total, fbs) in sorted(orders.items())]
    return summary, list(infos.values())


class CollectionService:
    """Суточные итоги заказов кабинета за `history_days`, по дню за запрос.

    Заказы спрашиваются по дню заказа: сутки — один ответ WB, повтор даёт те
    же сутки. Первая загрузка кабинета — три месяца, по `days_per_run` дней за
    проход, свежие дни первыми: окно расчёта заполняется раньше, чем месяцы
    для справки. Статистика WB дописывает заказ с опозданием, поэтому сутки
    считаются устоявшимися, только если их читали через `settle_hours` после
    полуночи; до того они перечитываются не чаще раза в `refresh_minutes`.
    """

    def __init__(
        self,
        session: AsyncSession,
        podsort: PodsortRepository,
        client: WBPodsortStatisticsClient,
        *,
        timezone: ZoneInfo,
        history_days: int,
        days_per_run: int,
        settle_hours: int,
        refresh_minutes: int,
    ) -> None:
        self.session = session
        self.podsort = podsort
        self.client = client
        self.timezone = timezone
        self.history_days = history_days
        self.days_per_run = days_per_run
        self.settle = timedelta(hours=settle_hours)
        self.refresh = timedelta(minutes=refresh_minutes)

    def history_start(self, today: date) -> date:
        return today - timedelta(days=self.history_days)

    def due_days(self, loaded: Mapping[date, datetime], *, now: datetime) -> list[date]:
        """Сутки, которые пора прочитать, от вчера к началу истории."""
        today = now.astimezone(self.timezone).date()
        due: list[date] = []
        day = today - timedelta(days=1)
        while day >= self.history_start(today):
            loaded_at = loaded.get(day)
            if loaded_at is None:
                due.append(day)
            else:
                settled_at = datetime.combine(day + timedelta(days=1), time(0), self.timezone) + self.settle
                if loaded_at < settled_at and loaded_at <= now - self.refresh:
                    due.append(day)
            day -= timedelta(days=1)
        return due

    async def pending(self, seller_id: uuid.UUID, *, now: datetime) -> list[date]:
        today = now.astimezone(self.timezone).date()
        loaded = await self.podsort.loaded_days(seller_id, self.history_start(today))
        return self.due_days(loaded, now=now)

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        stamp = now or datetime.now(UTC)
        today = stamp.astimezone(self.timezone).date()
        seller_key = str(seller_id)
        due = await self.pending(seller_id, now=stamp)
        batch = due[: self.days_per_run]
        # Сеть идёт до первой записи: держать транзакцию весь запрос незачем.
        await self.session.commit()

        loaded = orders = 0
        for day in batch:
            lines = await self.client.orders_on(seller_key, day)
            if not await self.podsort.still_tracked(seller_id):
                await self.session.rollback()
                return CollectionResult(loaded, orders, len(due) - loaded, skipped=True)
            counts, barcodes = summarize(lines)
            await self.podsort.replace_day(seller_id, day, counts, barcodes, now=stamp)
            # Сутки — отдельная транзакция: упавший на пятом дне проход не
            # должен перечитывать первые четыре.
            await self.session.commit()
            loaded += 1
            orders += len(lines)

        if not await self.podsort.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(loaded, orders, len(due) - loaded, skipped=True)
        await self.podsort.prune(seller_id, self.history_start(today))
        remaining = len(due) - loaded
        if remaining == 0:
            await self.podsort.finish_collection(seller_id, now=stamp)
        else:
            await self.podsort.clear_error(seller_id)
        await self.session.commit()
        return CollectionResult(loaded, orders, remaining)
