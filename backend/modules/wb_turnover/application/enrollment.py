import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

AUTOMATION_ID = "wb-turnover"
TITLE = "Оборачиваемость товаров Wildberries"
DESCRIPTION = (
    "Следит за запасом подключённых селлеров и предупреждает в Telegram, когда его хватает меньше чем на десять дней."
)


class TurnoverEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, turnover: TurnoverRepository) -> None:
        self.turnover = turnover

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.turnover.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.turnover.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.turnover.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.turnover.purge_seller(seller_id)
