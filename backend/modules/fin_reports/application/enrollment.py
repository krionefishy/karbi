import uuid

from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.application import AutomationEnrollment

AUTOMATION_ID = "fin-reports"
TITLE = "Финансовые отчёты"
DESCRIPTION = (
    "ОПиУ по неделям и месяцам из отчётов реализации Wildberries: выручка до и после СПП, комиссия, логистика, "
    "реклама, удержания, себестоимость и валовая маржа — по каждому кабинету и сводно, с выгрузкой в Excel."
)


class FinReportsEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, repository: FinReportsRepository) -> None:
        self.repository = repository

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.repository.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.repository.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        """Себестоимость остаётся: кабинет вернут — загружать файл заново не придётся."""
        await self.repository.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.repository.purge(seller_id)
