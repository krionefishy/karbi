import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository

AUTOMATION_ID = "wb-returns"
TITLE = "Возвраты WB"
DESCRIPTION = (
    "Возвраты товаров продавцу и заявки покупателей на возврат по кабинету: что едет в ПВЗ, что уже "
    "готово к выдаче и сколько дней осталось до платного хранения. Уведомления в Telegram с адресом "
    "ПВЗ и кодом получения."
)


class ReturnsEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, returns: ReturnsRepository) -> None:
        self.returns = returns

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.returns.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.returns.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.returns.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.returns.purge_seller(seller_id)
