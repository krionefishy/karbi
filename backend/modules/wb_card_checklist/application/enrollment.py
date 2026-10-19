from backend.modules.wb_card_checklist.infrastructure.postgres import ChecklistRepository
from backend.modules.wb_core.application import TrackedEnrollment

AUTOMATION_ID = "wb-card-checklist"
TITLE = "Чек-лист карточек Wildberries"
DESCRIPTION = (
    "Показывает по карточкам с остатком, что видно в данных WB: описание, характеристики, фото, видео, "
    "скидку и отзывы. Таблица выгружается в Excel."
)


class ChecklistEnrollment(TrackedEnrollment[ChecklistRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
