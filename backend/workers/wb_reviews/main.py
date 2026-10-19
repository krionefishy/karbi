import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_reviews.infrastructure.wb import WBFeedbackClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_reviews.review_consumer import ReviewSyncConsumer
from backend.workers.wb_reviews.worker import WBReviewsWorker


class WBReviewsWorkerApplication(WorkerApplication[WBReviewsWorker]):
    def build_worker(self) -> WBReviewsWorker:
        self.review_consumer = self._create_consumer()
        return WBReviewsWorker(
            self.database,
            self.settings.worker.poll_interval_seconds,
            self.settings.worker.review_sync_hour,
            self.settings.worker.review_sync_minute,
            self.settings.worker.review_sync_timezone,
            self.settings.kafka.enabled,
            job_max_attempts=self.settings.worker.job_max_attempts,
            run_max_age_seconds=self.settings.worker.run_max_age_seconds,
        )

    def _create_consumer(self) -> ReviewSyncConsumer | None:
        """Консьюмер каталога отсюда уехал в wb-core-worker: каталог нужен всем, а не отзывам."""
        if not self.settings.kafka.enabled:
            return None
        worker = self.settings.worker
        gateway = EgressGateway(self.settings.egress)
        return ReviewSyncConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.reviews",
            worker.feedback_page_size,
            lease_seconds=worker.job_lease_seconds,
            max_attempts=worker.job_max_attempts,
            retry_backoff_seconds=worker.job_retry_backoff_seconds,
            client=WBFeedbackClient(gateway, page_size=worker.feedback_page_size),
        )

    def connect_options(self) -> dict[str, Any]:
        database = self.settings.database
        return {"echo": database.echo, "pool_size": database.pool_size, "max_overflow": database.max_overflow}

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        if self.review_consumer is None:
            return {}
        return {"wb-review-consumer": self.review_consumer.run}


if __name__ == "__main__":
    asyncio.run(run_application(WBReviewsWorkerApplication))
