import logging
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_turnover.domain import TurnoverRow, compute_turnover
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

# A snapshot older than this is not "the current stock" any more; better to say
# we do not know than to divide by a figure from last week.
STOCK_FRESHNESS_DAYS = 1


class CalculationService:
    """Recomputes the metric for one seller from what has been collected."""

    def __init__(self, session: AsyncSession, turnover: TurnoverRepository, *, window_days: int) -> None:
        self.session = session
        self.turnover = turnover
        self.window_days = window_days
        self.logger = logging.getLogger("wb.turnover.calculation")

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
            # Every article we know anything about: one with orders but no stock
            # row is exactly the case the alert exists for.
            for article in sorted(set(stocks) | set(orders))
        ]
        if not await self.turnover.still_tracked(seller_id):
            # The seller list was snapshotted when the run claimed its slot; a
            # purge since then must not be overwritten with resurrected metric.
            self.logger.warning("turnover_seller_untracked_write_skipped", extra={"seller_id": str(seller_id)})
            await self.session.rollback()
            return []
        await self.turnover.upsert_turnover(rows)
        await self.session.commit()
        return rows
