from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.application import TrackedEnrollment

AUTOMATION_ID = "fin-reports"
TITLE = "Финансовые отчёты"
DESCRIPTION = (
    "ОПиУ по неделям и месяцам из отчётов реализации Wildberries: выручка до и после СПП, комиссия, логистика, "
    "реклама, удержания, себестоимость и валовая маржа — по каждому кабинету и сводно, с выгрузкой в Excel."
)


class FinReportsEnrollment(TrackedEnrollment[FinReportsRepository]):
    """Себестоимость остаётся после отключения: кабинет вернут — загружать файл заново не придётся."""

    automation_id = AUTOMATION_ID
    title = TITLE
