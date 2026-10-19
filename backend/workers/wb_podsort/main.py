import asyncio

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_podsort.infrastructure.wb import WBPodsortStatisticsClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_podsort.worker import PodsortWorker


class PodsortWorkerApplication(WorkerApplication[PodsortWorker]):
    """Отдельный процесс подсорта: догружает суточные итоги заказов подключённых кабинетов."""

    def build_worker(self) -> PodsortWorker:
        return PodsortWorker(
            self.database, WBPodsortStatisticsClient(EgressGateway(self.settings.egress)), self.settings
        )


if __name__ == "__main__":
    asyncio.run(run_application(PodsortWorkerApplication))
