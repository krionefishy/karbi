import logging
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.domain import TurnoverRow, compute_turnover
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

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
            for article in sorted((set(stocks) | set(orders)) & await self._listed(seller_id))
        ]
        if not await self.turnover.still_tracked(seller_id):
            self.logger.warning("turnover_seller_untracked_write_skipped", extra={"seller_id": str(seller_id)})
            await self.session.rollback()
            return []
        await self.turnover.upsert_turnover(rows)
        dropped = await self.turnover.drop_turnover_except(seller_id, day, {row.article for row in rows})
        if dropped:
            self.logger.info("turnover_rows_dropped", extra={"seller_id": str(seller_id), "dropped": dropped})
        await self.session.commit()
        return rows
