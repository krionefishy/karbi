import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.modules.wb_turnover.infrastructure.wb import (
    OrderRow,
    WBAnalyticsClient,
    WBMarketplaceClient,
    WBStatisticsClient,
)

# Wildberries reports and accepts statistics dates in Moscow time without a zone.
MOSCOW = ZoneInfo("Europe/Moscow")
WAREHOUSE_REFRESH = timedelta(days=1)

FBS_COLLECTED = "collected"
FBS_ABSENT = "absent"
FBS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FbsStock:
    """Остаток FBS в двух разрезах: суммой на артикул и по складам.

    Оба берутся из одного обхода складов. Метрике нужна сумма — товар считается
    целиком, где бы он ни лежал; подсорту нужен разрез, потому что вопрос у него
    другой: чего не хватает именно в Казани.
    """

    totals: dict[str, tuple[int, int, int, int]]
    per_warehouse: dict[tuple[str, int], int]


@dataclass(frozen=True, slots=True)
class StockResult:
    """Outcome of one stock slot for one seller.

    The two halves are reported apart on purpose: «складов у продавца нет» and
    «Marketplace сейчас не отвечает» are indistinguishable in the data and mean
    opposite things.
    """

    articles: int
    fbs: str

    @property
    def fbs_failed(self) -> bool:
        return self.fbs == FBS_FAILED


class CollectionService:
    """Fills the two series the metric divides: stock now, orders over the window."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        statistics: WBStatisticsClient,
        analytics: WBAnalyticsClient,
        marketplace: WBMarketplaceClient,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.statistics = statistics
        self.analytics = analytics
        self.marketplace = marketplace
        self.logger = logging.getLogger("wb.turnover.collection")

    async def collect_stocks(self, seller_id: uuid.UUID, snapshot_date: date, slot: int) -> StockResult:
        """One stock snapshot: FBO from the analytics report, FBS from the seller's warehouses.

        The halves are written independently. FBO no longer depends on the
        Marketplace API at all, and an FBS outage neither discards a good WB
        reading nor overwrites the previous FBS figures with zeros — zeros
        would read as «товар кончился» and raise a false alarm.
        """
        seller_key = str(seller_id)
        known = await self._known_articles(seller_id)
        article_of = await self.sellers.list_chrt_articles(seller_id)
        # The reads are done. The requests below can take minutes on a large
        # catalog, and a transaction left open across them would hold its locks
        # for the whole walk.
        await self.session.commit()

        fbo: dict[str, list[int]] = {article: [0, 0, 0, 0] for article in known}
        for row in await self.analytics.stocks(seller_key):
            values = fbo.setdefault(row.article, [0, 0, 0, 0])
            values[0] += row.quantity
            values[2] += row.in_way_to_client
            values[3] += row.in_way_from_client
        for values in fbo.values():
            # The report no longer carries quantityFull. The column keeps its
            # meaning as a total including goods in transit, but it is now our
            # own derived number and the metric never reads it.
            values[1] = values[0] + values[2] + values[3]

        if not await self._still_ours(seller_id):
            return StockResult(0, FBS_ABSENT)
        # Zero rows for everything we know: WB lists a товар only while it has
        # stock or goods in transit, and without them the last non-zero snapshot
        # would pass for the current stock forever.
        await self.turnover.upsert_snapshots(
            seller_id,
            snapshot_date,
            slot,
            "fbo",
            {article: (values[0], values[1], values[2], values[3]) for article, values in fbo.items()},
        )
        await self.session.commit()

        return StockResult(len(fbo), await self._store_fbs(seller_id, seller_key, snapshot_date, slot, article_of))

    async def _store_fbs(
        self,
        seller_id: uuid.UUID,
        seller_key: str,
        snapshot_date: date,
        slot: int,
        article_of: dict[int, str],
    ) -> str:
        """Collect and write the FBS half, reporting why it is missing when it is."""
        try:
            fbs = await self._collect_fbs(seller_id, seller_key, article_of)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("turnover_fbs_unavailable", extra={"seller_id": str(seller_id), "error": str(error)})
            await self.session.rollback()
            return FBS_FAILED
        if fbs is None:
            return FBS_ABSENT
        if not await self._still_ours(seller_id):
            return FBS_ABSENT
        await self.turnover.upsert_snapshots(seller_id, snapshot_date, slot, "fbs", fbs.totals)
        await self.turnover.replace_warehouse_stocks(seller_id, fbs.per_warehouse)
        await self.session.commit()
        return FBS_COLLECTED

    async def _still_ours(self, seller_id: uuid.UUID) -> bool:
        """A purge may land while we are talking to WB; writing then would
        resurrect the very rows it deleted."""
        if await self.turnover.still_tracked(seller_id):
            return True
        self.logger.warning("turnover_seller_untracked_write_skipped", extra={"seller_id": str(seller_id)})
        await self.session.rollback()
        return False

    async def _collect_fbs(
        self,
        seller_id: uuid.UUID,
        seller_key: str,
        article_of: dict[int, str],
    ) -> FbsStock | None:
        """Declared stock at the seller's warehouses, or None when there is none to collect.

        Summed over every size of an article and every warehouse: the same
        товар lives under several chrtId at several addresses. The per-warehouse
        breakdown is kept from the same walk — it costs nothing here and cannot
        be recovered from the sum later.
        """
        if not article_of:
            self.logger.warning("В каталоге селлера %s нет размеров — FBS-остатки в этом срезе не собраны", seller_id)
            return None
        warehouses = await self._warehouses(seller_id, seller_key)
        if not warehouses:
            self.logger.warning("У селлера %s нет складов Marketplace — FBS-остатки в этом срезе не собраны", seller_id)
            return None
        chrt_ids = list(article_of)
        amounts: dict[str, int] = defaultdict(int)
        per_warehouse: dict[tuple[str, int], int] = defaultdict(int)
        for warehouse_id, _ in warehouses:
            declared = await self.marketplace.stocks(seller_key, warehouse_id, chrt_ids)
            for chrt_id, amount in declared.items():
                article = article_of.get(chrt_id)
                if article is not None:
                    amounts[article] += amount
                    per_warehouse[(article, warehouse_id)] += amount
        # A size WB stayed silent about holds nothing at that warehouse.
        return FbsStock(
            totals={
                article: (amounts.get(article, 0), amounts.get(article, 0), 0, 0)
                for article in set(article_of.values())
            },
            per_warehouse=dict(per_warehouse),
        )

    async def _warehouses(self, seller_id: uuid.UUID, seller_key: str) -> list[tuple[int, str]]:
        tracked = await self.turnover.tracked(seller_id)
        synced_at = tracked.warehouses_synced_at if tracked else None
        if synced_at is not None and datetime.now(UTC) - synced_at < WAREHOUSE_REFRESH:
            return await self.turnover.warehouses(seller_id)
        warehouses = [(item.id, item.name) for item in await self.marketplace.warehouses(seller_key)]
        await self.turnover.replace_warehouses(seller_id, warehouses)
        return warehouses

    async def collect_orders(
        self, seller_id: uuid.UUID, now: datetime, *, backfill_days: int, overlap_hours: int
    ) -> int:
        """Pull orders changed since the watermark and move it forward.

        The watermark only advances on success, so a failed run repeats the same
        window instead of leaving a hole, and the overlap covers orders that
        changed while the previous pull was running.
        """
        seller_key = str(seller_id)
        tracked = await self.turnover.tracked(seller_id)
        watermark = tracked.orders_watermark if tracked else None
        if watermark is None:
            # A seller connected today still gets a full window: unlike stock,
            # order history is served on request.
            date_from = now - timedelta(days=backfill_days)
        else:
            date_from = watermark - timedelta(hours=overlap_hours)
        # Close the read transaction before the network walk; the write below
        # opens its own short one.
        await self.session.commit()

        rows = await self.statistics.orders(seller_key, date_from.astimezone(MOSCOW))
        if not rows:
            return 0
        if not await self.turnover.still_tracked(seller_id):
            # A purge landed while we were talking to WB; writing now would
            # resurrect the very rows it deleted.
            self.logger.warning("turnover_seller_untracked_write_skipped", extra={"seller_id": str(seller_id)})
            await self.session.rollback()
            return 0
        await self.turnover.upsert_orders(
            seller_id,
            [
                {
                    "srid": row.srid,
                    "article": row.article,
                    "order_date": row.order_date,
                    "last_change_date": self._aware(row.last_change_date),
                    "is_cancel": row.is_cancel,
                    "price": row.price,
                    "warehouse_type": row.warehouse_type,
                }
                for row in rows
            ],
        )
        await self.turnover.set_watermark(seller_id, max(self._aware(row.last_change_date) for row in rows))
        await self.session.commit()
        return len(rows)

    async def collect_region_orders(self, seller_id: uuid.UUID, today: date, *, horizon_days: int, days: int) -> int:
        """Собрать спрос по округам за сутки, которых ещё нет в базе.

        Ходим по суткам: `dateFrom = дата, flag = 1` отдаёт заказы, сделанные в
        этот день. Одним запросом за полгода нельзя — у метода нет верхней
        границы периода, и «всё с марта» он обрежет молча. Сутки — это один
        запрос, он повторяем, и потерять его при рестарте не жалко.

        Отсчёт ведётся от **вчера**: сегодняшний день ещё меняется, и считать
        его наравне с прожитыми — то же самое, что занижать темп сегодняшними
        тремя часами.

        Собранное описывается непрерывным отрезком `[filled_from, filled_to]`.
        Сначала отрезок растёт вперёд, до вчера, — так закрываются сутки,
        пропущенные простоем; потом назад, до горизонта. Границы двигаются
        только после записи дня, поэтому прерванный сбор продолжается с того же
        места, а не начинается заново.

        Возвращает, сколько суток добавлено. Когда добавлять нечего, шаг не
        ходит в WB вовсе.
        """
        yesterday = today - timedelta(days=1)
        tracked = await self.turnover.tracked(seller_id)
        filled_from = tracked.regions_filled_from if tracked else None
        filled_to = tracked.regions_filled_to if tracked else None
        await self.session.commit()

        wanted = self._region_days(
            yesterday,
            floor=today - timedelta(days=horizon_days),
            filled_from=filled_from,
            filled_to=filled_to,
            limit=days,
        )
        collected = 0
        for day in wanted:
            rows = await self.statistics.orders_on(str(seller_id), day)
            if not await self._still_ours(seller_id):
                return collected
            await self.turnover.replace_region_day(seller_id, day, self._by_district(rows))
            await self.turnover.widen_region_range(seller_id, day)
            await self.session.commit()
            collected += 1
        return collected

    @staticmethod
    def _region_days(
        yesterday: date,
        *,
        floor: date,
        filled_from: date | None,
        filled_to: date | None,
        limit: int,
    ) -> list[date]:
        """Какие сутки собирать следующими — сначала свежие, потом в глубину.

        Вперёд раньше, чем вглубь: сутки, пропущенные простоем, нужнее самых
        старых, а горизонт всё равно никуда не уедет.
        """
        if filled_from is None or filled_to is None:
            # Отрезка ещё нет — начинаем его от вчера и растим назад.
            deepest = [yesterday - timedelta(days=offset) for offset in range(limit)]
            return [day for day in deepest if day >= floor]
        forward = [filled_to + timedelta(days=offset) for offset in range(1, limit + 1)]
        wanted = [day for day in forward if day <= yesterday]
        backward = [filled_from - timedelta(days=offset) for offset in range(1, limit + 1)]
        wanted += [day for day in backward if day >= floor]
        return wanted[:limit]

    @staticmethod
    def _by_district(rows: list[OrderRow]) -> dict[tuple[str, str], tuple[int, int]]:
        """Заказы и отмены по паре «артикул + округ».

        Округ хранится ровно тем, что назвал WB, — пустая строка в том числе.
        Отбрасывать безымянные строки значило бы, что «Итого» в отчёте меньше,
        чем заказов на самом деле; куда их отнести, решает отчёт.
        """
        counts: dict[tuple[str, str], tuple[int, int]] = {}
        for row in rows:
            key = (row.article, row.district)
            orders, cancelled = counts.get(key, (0, 0))
            counts[key] = (orders, cancelled + 1) if row.is_cancel else (orders + 1, cancelled)
        return counts

    @staticmethod
    def _aware(moment: datetime) -> datetime:
        """WB sends Moscow time without a zone; storing it as UTC would shift it three hours."""
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=MOSCOW)

    async def _known_articles(self, seller_id: uuid.UUID) -> set[str]:
        """Cards WB still lists, and only those.

        An archived card is one the seller withdrew; a `feedback_only` card is
        one WB no longer returns in the catalog at all. Neither can be
        restocked, so a turnover row for them is noise — for one seller they
        were 243 of 1035 rows, every one of them «нет остатка».
        """
        return {article.article for article in await self.sellers.list_articles(seller_id) if article.state == "active"}
