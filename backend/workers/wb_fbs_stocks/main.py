import asyncio

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_fbs_stocks.infrastructure.wb import WBFbsStocksClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_fbs_stocks.worker import FbsStocksWorker


class FbsStocksWorkerApplication(WorkerApplication[FbsStocksWorker]):
    """Отдельный процесс таблицы остатков FBS: опрашивает склады кабинетов раз в N минут."""

    def build_worker(self) -> FbsStocksWorker:
        return FbsStocksWorker(self.database, WBFbsStocksClient(EgressGateway(self.settings.egress)), self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(FbsStocksWorkerApplication))
