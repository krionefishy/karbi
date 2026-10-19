import asyncio

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_fbs_distribution.application import DisconnectedSource
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_fbs_distribution.worker import FbsDistributionWorker


class FbsDistributionWorkerApplication(WorkerApplication[FbsDistributionWorker]):
    """Отдельный процесс автоматизации распределения FBS.

    Свой воркер, а не шаг в оборачиваемости: та читает заявленные остатки, а
    этот ими управляет. Ключи и бюджет запросов к WB живут на шлюзе wb-egress.
    """

    def build_worker(self) -> FbsDistributionWorker:
        gateway = EgressGateway(self.settings.egress)
        return FbsDistributionWorker(
            self.database, WBFbsMarketplaceClient(gateway), DisconnectedSource(), self.settings
        )


if __name__ == "__main__":
    asyncio.run(run_application(FbsDistributionWorkerApplication))
