import asyncio

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBFinanceClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_fbs_penalties.worker import PenaltiesWorker


class PenaltiesWorkerApplication(WorkerApplication[PenaltiesWorker]):
    """Отдельный процесс штрафов: раз в сутки перечитывает фин. отчёт подключённых кабинетов."""

    def build_worker(self) -> PenaltiesWorker:
        return PenaltiesWorker(self.database, WBFinanceClient(EgressGateway(self.settings.egress)), self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(PenaltiesWorkerApplication))
