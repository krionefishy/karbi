import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_fbs_stocks.infrastructure.postgres import FbsStocksRepository

AUTOMATION_ID = "wb-fbs-stocks"
TITLE = "Остатки FBS по складам"
DESCRIPTION = (
    "Таблица остатков по баркодам на складах продавца: столбцы сгруппированы по фулфилментам и "
    "округам, нули подсвечены. Обновляется по расписанию и по кнопке, выгружается в Excel."
)


class FbsStocksEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, stocks: FbsStocksRepository) -> None:
        self.stocks = stocks

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.stocks.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.stocks.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.stocks.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.stocks.purge_seller(seller_id)
