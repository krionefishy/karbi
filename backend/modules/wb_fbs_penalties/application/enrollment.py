from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository

AUTOMATION_ID = "wb-fbs-penalties"
TITLE = "Штрафы FBS"
DESCRIPTION = (
    "Штрафы и удержания из фин. отчёта WB по кабинету: к каждой строке — склад продавца, с которого "
    "ушёл заказ, и поставка. Проверка по стикеру или номеру заказа, выгрузка в Excel за период."
)


class PenaltiesEnrollment(TrackedEnrollment[PenaltiesRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
