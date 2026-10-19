from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository

AUTOMATION_ID = "wb-returns"
TITLE = "Возвраты WB"
DESCRIPTION = (
    "Возвраты товаров продавцу и заявки покупателей на возврат по кабинету: что едет в ПВЗ, что уже "
    "готово к выдаче и сколько дней осталось до платного хранения. Уведомления в Telegram с адресом "
    "ПВЗ и списком товаров."
)


class ReturnsEnrollment(TrackedEnrollment[ReturnsRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
