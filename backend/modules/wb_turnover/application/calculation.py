import logging
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.domain import TurnoverRow, compute_turnover
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

# A snapshot older than this is not "the current stock" any more; better to say
# we do not know than to divide by a figure from last week.
STOCK_FRESHNESS_DAYS = 1


class CalculationService:
    """Recomputes the metric for one seller from what has been collected."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        *,
        window_days: int,
        min_photos: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.window_days = window_days
        self.min_photos = min_photos
        self.logger = logging.getLogger("wb.turnover.calculation")

    async def _listed(self, seller_id: uuid.UUID) -> set[str]:
        return {
            article.article
            for article in await self.sellers.list_articles(seller_id)
            if article.state == "active" and self._shown(article)
        }

    def _shown(self, article: Article) -> bool:
        """Карточка с парой фото в выдаче WB почти не показывается, и считать ей
        оборачиваемость незачем — товар стоит не потому, что кончается запас.

        Число фото у нас появляется из синка каталога. Пока его нет (`None`),
        товар не исключается: погасить уведомления по всему ассортименту из-за
        того, что мы ещё не смотрели, — худшая из двух ошибок.
        """
        return article.photo_count is None or article.photo_count >= self.min_photos

    async def calculate(self, seller_id: uuid.UUID, day: date) -> list[TurnoverRow]:
        window_start = day - timedelta(days=self.window_days)
        yesterday = day - timedelta(days=1)
        stocks = await self.turnover.latest_stock(seller_id, day - timedelta(days=STOCK_FRESHNESS_DAYS))
        # The average ends where the orders window does: today's snapshots would
        # otherwise sneak into a metric whose sales side stops yesterday.
        averages = await self.turnover.average_stock(seller_id, window_start, yesterday)
        orders = await self.turnover.orders_in_window(seller_id, window_start, yesterday)

        rows = [
            compute_turnover(
                seller_id=seller_id,
                article=article,
                day=day,
                stock=stocks.get(article),
                orders=orders.get(article),
                average_stock=averages.get(article, (0.0, 0))[0],
                stock_days=averages.get(article, (0.0, 0))[1],
                window_days=self.window_days,
            )
            # Every article we know anything about — an article with orders but
            # no stock row is exactly the case the alert exists for — as long as
            # WB still lists the card. Orders and the stock report both keep
            # mentioning товары the seller has already withdrawn.
            for article in sorted((set(stocks) | set(orders)) & await self._listed(seller_id))
        ]
        if not await self.turnover.still_tracked(seller_id):
            # The seller list was snapshotted when the run claimed its slot; a
            # purge since then must not be overwritten with resurrected metric.
            self.logger.warning("turnover_seller_untracked_write_skipped", extra={"seller_id": str(seller_id)})
            await self.session.rollback()
            return []
        await self.turnover.upsert_turnover(rows)
        dropped = await self.turnover.drop_turnover_except(seller_id, day, {row.article for row in rows})
        if dropped:
            self.logger.info("turnover_rows_dropped", extra={"seller_id": str(seller_id), "dropped": dropped})
        await self.session.commit()
        return rows
