import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_podsort.infrastructure.postgres import PodsortRepository

AUTOMATION_ID = "wb-podsort"
TITLE = "Подсорт WB"
DESCRIPTION = (
    "Сколько везти на склады WB по регионам: продажи за 7 или 14 дней на срок покрытия минус то, что уже "
    "лежит на складах WB региона. Все подключённые кабинеты в одной книге — сводный лист по регионам "
    "с ручными столбцами для логиста и склада, лист на кабинет."
)


class PodsortEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, podsort: PodsortRepository) -> None:
        self.podsort = podsort

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.podsort.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.podsort.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.podsort.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.podsort.purge_seller(seller_id)
