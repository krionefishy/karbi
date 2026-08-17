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
from backend.workers.wb_reviews.review_consumer import ReviewSyncConsumer
from backend.workers.wb_reviews.worker import WBReviewsWorker


class WBReviewsWorkerApplication:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.worker = WBReviewsWorker(
            self.database,
            self.settings.worker.poll_interval_seconds,
            self.settings.worker.review_sync_hour,
            self.settings.worker.review_sync_timezone,
            self.settings.kafka.enabled,
        )
        self.catalog_consumer, self.review_consumer = self._create_consumers()
        self.container = make_async_container(
            *WORKER_PROVIDERS,
            context={Settings: self.settings, Database: self.database},
        )

    def _create_consumers(self) -> tuple[CatalogSyncConsumer | None, ReviewSyncConsumer | None]:
        if not self.settings.kafka.enabled:
            return None, None
        cipher = CredentialCipher(
            self.settings.security.credential_encryption_keys,
            self.settings.security.credential_fingerprint_key,
        )
        return (
            CatalogSyncConsumer(
                self.database,
                cipher,
                self.settings.kafka.bootstrap_servers,
                f"{self.settings.kafka.consumer_group}.wb.catalog",
            ),
            ReviewSyncConsumer(
                self.database,
                cipher,
                self.settings.kafka.bootstrap_servers,
                f"{self.settings.kafka.consumer_group}.wb.reviews",
                self.settings.worker.feedback_page_size,
            ),
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
            tasks: list[asyncio.Task[None]] = []
            if self.settings.kafka.enabled:
                if self.catalog_consumer is None or self.review_consumer is None:
                    raise RuntimeError("WB consumers are not configured")
                await ensure_topics(
                    bootstrap_servers=self.settings.kafka.bootstrap_servers,
                    partitions=self.settings.kafka.topic_partitions,
                    replication_factor=self.settings.kafka.topic_replication_factor,
                )
                tasks = [
                    asyncio.create_task(self.catalog_consumer.run(), name="wb-catalog-consumer"),
                    asyncio.create_task(self.review_consumer.run(), name="wb-review-consumer"),
                ]
            try:
                await self.worker.run()
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
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
