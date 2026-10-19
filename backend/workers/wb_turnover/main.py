import asyncio

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_turnover.infrastructure.wb import WBAnalyticsClient, WBMarketplaceClient, WBStatisticsClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_turnover.worker import TurnoverWorker


class TurnoverWorkerApplication(WorkerApplication[TurnoverWorker]):
    """Collects stock and orders on a schedule and hands alerts to notifications.

    No Kafka: a step here is a couple of requests per seller, and a missed slot
    is covered by the next one a few hours later. What must not be lost — the
    orders watermark and the digest — is protected by the database instead.
    """

    def build_worker(self) -> TurnoverWorker:
        gateway = EgressGateway(self.settings.egress)
        return TurnoverWorker(
            self.database,
            WBStatisticsClient(gateway),
            WBAnalyticsClient(gateway),
            WBMarketplaceClient(gateway),
            self.settings,
        )


if __name__ == "__main__":
    asyncio.run(run_application(TurnoverWorkerApplication))
