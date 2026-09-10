import uuid
from datetime import date, timedelta

from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

# Снимки берутся четыре раза в сутки. Если последний старше двух суток, сбор
# стоит, и такой «текущий» остаток соседям отдавать нельзя.
FRESH_FOR_DAYS = 2


class CurrentStockReader:
    """Текущий остаток по артикулам — FBO и FBS вместе — для соседних модулей.

    Остатки собирает оборачиваемость; дублировать её сбор ради отбора товаров
    в чек-листе значило бы тратить бюджет WB дважды на одни и те же числа.
    """

    def __init__(self, turnover: TurnoverRepository) -> None:
        self.turnover = turnover

    async def stock(self, seller_id: uuid.UUID, today: date) -> dict[str, int] | None:
        """None — селлер не подключён к оборачиваемости; пусто — свежих снимков нет."""
        if await self.turnover.tracked(seller_id) is None:
            return None
        latest = await self.turnover.latest_stock(seller_id, today - timedelta(days=FRESH_FOR_DAYS))
        return {article: item.total for article, item in latest.items()}
