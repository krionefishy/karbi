import uuid

from backend.modules.wb_card_checklist.infrastructure.postgres import ChecklistRepository
from backend.modules.wb_core.application import AutomationEnrollment

AUTOMATION_ID = "wb-card-checklist"
TITLE = "Чек-лист карточек Wildberries"
DESCRIPTION = (
    "Сверяет карточки с остатком по двенадцати пунктам готовности: что видно в данных WB, отмечает само, "
    "остальное — менеджер. Таблица выгружается в Excel."
)


class ChecklistEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, checklist: ChecklistRepository) -> None:
        self.checklist = checklist

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.checklist.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.checklist.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.checklist.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.checklist.purge_seller(seller_id)
