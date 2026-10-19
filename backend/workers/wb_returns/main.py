import asyncio
from collections.abc import Awaitable, Callable

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_returns.infrastructure.wb import WBClaimsClient, WBReturnsReportClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_returns.commands import CommandConsumer
from backend.workers.wb_returns.worker import ReturnsWorker


class ReturnsWorkerApplication(WorkerApplication[ReturnsWorker]):
    """Отдельный процесс возвратов: сбор и уведомления по расписанию плюс ответы на команды бота."""

    pool_size = 3

    def build_worker(self) -> ReturnsWorker:
        gateway = EgressGateway(self.settings.egress)
        self.commands = self._create_consumer()
        return ReturnsWorker(self.database, WBReturnsReportClient(gateway), WBClaimsClient(gateway), self.settings)

    def _create_consumer(self) -> CommandConsumer | None:
        if not self.settings.kafka.enabled:
            return None
        return CommandConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.returns.commands",
            settings=self.settings,
        )

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        if self.commands is None:
            return {}
        return {"wb-returns-commands": self.commands.run}


if __name__ == "__main__":
    asyncio.run(run_application(ReturnsWorkerApplication))
