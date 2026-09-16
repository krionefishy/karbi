import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository

AUTOMATION_ID = "wb-fbs-penalties"
TITLE = "Штрафы FBS"
DESCRIPTION = (
    "Штрафы и удержания из фин. отчёта WB по кабинету: к каждой строке — склад продавца, с которого "
    "ушёл заказ, и поставка. Проверка по стикеру или номеру заказа, выгрузка в Excel за период."
)


class PenaltiesEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, penalties: PenaltiesRepository) -> None:
        self.penalties = penalties

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.penalties.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.penalties.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.penalties.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.penalties.purge_seller(seller_id)
