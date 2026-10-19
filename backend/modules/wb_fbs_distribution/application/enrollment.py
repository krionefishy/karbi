from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository

AUTOMATION_ID = "wb-fbs-distribution"
TITLE = "Распределение остатков FBS"
DESCRIPTION = (
    "Делит физический остаток товара между виртуальными FBS-складами кабинета и публикует результат в Wildberries."
)


class FbsDistributionEnrollment(TrackedEnrollment[FbsDistributionRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
