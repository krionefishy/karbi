from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_fbs_stocks.infrastructure.postgres import FbsStocksRepository

AUTOMATION_ID = "wb-fbs-stocks"
TITLE = "Остатки FBS по складам"
DESCRIPTION = (
    "Таблица остатков по баркодам на складах продавца: столбцы сгруппированы по фулфилментам и "
    "округам, нули подсвечены. Обновляется по расписанию и по кнопке, выгружается в Excel."
)


class FbsStocksEnrollment(TrackedEnrollment[FbsStocksRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
