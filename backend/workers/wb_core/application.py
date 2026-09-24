import asyncio
import logging
import signal
from collections.abc import Callable, Coroutine

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_core.application import MirrorService
from backend.modules.wb_core.infrastructure.wb import (
    EgressGateway,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBWarehouseRemainsClient,
)
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.wb_core.catalog_consumer import CatalogSyncConsumer
from backend.workers.wb_core.worker import WBCoreWorker


class WBCoreWorkerApplication:
    """Отдельный процесс зеркала WB: расписание по всем селлерам плюс консьюмер каталога.

    Единственная точка, откуда наполняются каталог, остатки и отзывы в wb_core;
    автоматизации их только читают. Поэтому у процесса нет другой работы, а
    консьюмер прикрыт своим супервизором: heartbeat ловит только внешний цикл.
    """

    # How long a crashed consumer waits before coming back; keeps a hard
    # failure from turning into a busy restart loop.
    consumer_restart_delay_seconds: float = 5.0

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.logger = logging.getLogger("wb.core.application")
        self.database = Database()
        # Троттлинг и ключи живут на шлюзе wb-egress; воркер знает только seller_id.
        gateway = EgressGateway(self.settings.egress)
        self.mirror = MirrorService(
            self.database,
            content=WBContentClient(gateway),
            analytics=WBAnalyticsClient(gateway),
            marketplace=WBMarketplaceClient(gateway),
            feedbacks=WBFeedbackClient(gateway, page_size=self.settings.worker.feedback_page_size),
            chats=WBChatClient(gateway),
            remains=WBWarehouseRemainsClient(gateway),
            orders_history_months=self.settings.core_mirror.orders_history_months,
            chats_history_days=self.settings.core_mirror.chats_history_days,
            chats_pages_per_run=self.settings.core_mirror.chats_pages_per_run,
        )
        self.worker = WBCoreWorker(self.database, self.mirror, self.settings)
        self.catalog_consumer = self._create_consumer()

    def _create_consumer(self) -> CatalogSyncConsumer | None:
        if not self.settings.kafka.enabled:
            return None
        # Та же группа, что была у консьюмера в воркере отзывов: топик не
        # перечитывается с начала после переезда.
        return CatalogSyncConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.catalog",
            mirror=self.mirror,
        )

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, pool_size=2, max_overflow=2)
            tasks: list[asyncio.Task[None]] = []
            if self.catalog_consumer is not None:
                tasks = [
                    asyncio.create_task(
                        self._supervise("wb-catalog-consumer", self._run_consumer),
                        name="wb-catalog-consumer",
                    )
                ]
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
        """Топики — забота консьюмера, а не расписания: недоступный Kafka не должен
        останавливать сбор остатков, которому Kafka не нужен."""
        assert self.catalog_consumer is not None
        await ensure_topics(
            bootstrap_servers=self.settings.kafka.bootstrap_servers,
            partitions=self.settings.kafka.topic_partitions,
            replication_factor=self.settings.kafka.topic_replication_factor,
        )
        await self.catalog_consumer.run()

    async def _supervise(self, name: str, factory: Callable[[], Coroutine[None, None, None]]) -> None:
        """Keep the consumer alive for the life of the worker: a consumer loop
        only returns by raising, and without supervision that death is silent."""
        while True:
            try:
                await factory()
                self.logger.error("consumer_exited_unexpectedly", extra={"consumer": name})
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("consumer_crashed", extra={"consumer": name})
            await asyncio.sleep(self.consumer_restart_delay_seconds)

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_worker() -> None:
    application = WBCoreWorkerApplication()
    application.install_signal_handlers()
    await application.run()
