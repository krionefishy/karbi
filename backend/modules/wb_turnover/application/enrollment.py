from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

AUTOMATION_ID = "wb-turnover"
TITLE = "Оборачиваемость товаров Wildberries"
DESCRIPTION = (
    "Следит за запасом подключённых селлеров и предупреждает в Telegram, когда его хватает меньше чем на десять дней."
)


class TurnoverEnrollment(TrackedEnrollment[TurnoverRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
