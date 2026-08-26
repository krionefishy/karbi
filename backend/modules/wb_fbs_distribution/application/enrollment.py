import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository

AUTOMATION_ID = "wb-fbs-distribution"
TITLE = "Распределение остатков FBS"
DESCRIPTION = (
    "Делит физический остаток товара между виртуальными FBS-складами кабинета и публикует результат в Wildberries."
)


class FbsDistributionEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, distribution: FbsDistributionRepository) -> None:
        self.distribution = distribution

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.distribution.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.distribution.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.distribution.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.distribution.purge_seller(seller_id)
