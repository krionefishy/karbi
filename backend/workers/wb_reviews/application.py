import asyncio
import signal

from dishka import make_async_container

from backend.infrastructure.logging import configure_logging
from backend.shared.di import WORKER_PROVIDERS
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.security import CredentialCipher
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.wb_reviews.catalog_consumer import CatalogSyncConsumer
from backend.workers.wb_reviews.worker import WBReviewsWorker


class WBReviewsWorkerApplication:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.worker = WBReviewsWorker(self.settings.worker.poll_interval_seconds)
        self.catalog_consumer = self._create_catalog_consumer()
        self.container = make_async_container(
            *WORKER_PROVIDERS,
            context={
                Settings: self.settings,
                Database: self.database,
            },
        )

    def _create_catalog_consumer(self) -> CatalogSyncConsumer | None:
        if not self.settings.kafka.enabled:
            return None
        return CatalogSyncConsumer(
            self.database,
            CredentialCipher(
                self.settings.security.credential_encryption_keys, self.settings.security.credential_fingerprint_key
            ),
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.catalog",
        )

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(
                self.settings.database.url,
                echo=self.settings.database.echo,
                pool_size=self.settings.database.pool_size,
                max_overflow=self.settings.database.max_overflow,
            )
            if self.settings.kafka.enabled:
                if self.catalog_consumer is None:
                    raise RuntimeError("WB catalog consumer is not configured")
                await ensure_topics(
                    bootstrap_servers=self.settings.kafka.bootstrap_servers,
                    partitions=self.settings.kafka.topic_partitions,
                    replication_factor=self.settings.kafka.topic_replication_factor,
                )
                consumer_task = asyncio.create_task(self.catalog_consumer.run(), name="wb-catalog-consumer")
                try:
                    await self.worker.run()
                finally:
                    consumer_task.cancel()
                    await asyncio.gather(consumer_task, return_exceptions=True)
            else:
                await self.worker.run()
        finally:
            await self.container.close()
            await self.database.disconnect()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_name, self.worker.stop)


async def run_worker() -> None:
    application = WBReviewsWorkerApplication()
    application.install_signal_handlers()
    await application.run()
