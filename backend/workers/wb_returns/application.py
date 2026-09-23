import asyncio
import signal
from collections.abc import Callable, Coroutine

import structlog

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_returns.infrastructure.wb import WBClaimsClient, WBReturnsReportClient
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.wb_returns.commands import CommandConsumer
from backend.workers.wb_returns.worker import ReturnsWorker


class ReturnsWorkerApplication:
    """Отдельный процесс возвратов: сбор и уведомления по расписанию плюс ответы на команды бота."""

    consumer_restart_delay_seconds: float = 5.0

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        gateway = EgressGateway(self.settings.egress)
        self.worker = ReturnsWorker(
            self.database, WBReturnsReportClient(gateway), WBClaimsClient(gateway), self.settings
        )
        self.commands = self._create_consumer()
        self.logger = structlog.get_logger("wb_returns_application")

    def _create_consumer(self) -> CommandConsumer | None:
        if not self.settings.kafka.enabled:
            return None
        return CommandConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.returns.commands",
            settings=self.settings,
        )

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, pool_size=3, max_overflow=2)
            tasks: list[asyncio.Task[None]] = []
            if self.commands is not None:
                tasks = [asyncio.create_task(self._supervise("wb-returns-commands", self._run_consumer))]
            try:
                await self.worker.run()
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.database.disconnect()

    async def _run_consumer(self) -> None:
        """Топики — забота консьюмера: недоступный Kafka не должен останавливать сбор."""
        assert self.commands is not None
        await ensure_topics(
            bootstrap_servers=self.settings.kafka.bootstrap_servers,
            partitions=self.settings.kafka.topic_partitions,
            replication_factor=self.settings.kafka.topic_replication_factor,
        )
        await self.commands.run()

    async def _supervise(self, name: str, factory: Callable[[], Coroutine[None, None, None]]) -> None:
        while True:
            try:
                await factory()
                self.logger.error("consumer_exited_unexpectedly", consumer=name)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("consumer_crashed", consumer=name)
            await asyncio.sleep(self.consumer_restart_delay_seconds)

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_worker() -> None:
    application = ReturnsWorkerApplication()
    application.install_signal_handlers()
    await application.run()
