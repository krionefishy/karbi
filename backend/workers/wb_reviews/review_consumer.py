import asyncio
import json
import logging
import uuid
from datetime import date

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import CatalogCard
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.modules.wb_reviews.infrastructure.wb import (
    FeedbackAggregation,
    WBFeedbackClient,
    WBFeedbackPermanentError,
    WBFeedbackTemporaryError,
)
from backend.shared.kafka_streams.topics import WBReviewsTopics
from backend.shared.security import CredentialCipher, CredentialDecryptionError
from backend.storage.pg import Database

NO_RATINGS = (0, 0, 0, 0, 0)


class ReviewSyncConsumer:
    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher,
        bootstrap_servers: str,
        group_id: str,
        page_size: int,
        request_interval_seconds: float = 1.0,
        retry_wait_seconds: int = 600,
        lease_seconds: int = 1800,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 300,
        client: WBFeedbackClient | None = None,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.feedback_client = client or WBFeedbackClient(
            page_size=page_size,
            request_interval_seconds=request_interval_seconds,
            max_retry_wait_seconds=retry_wait_seconds,
        )
        self.logger = logging.getLogger("wb.reviews.consumer")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            WBReviewsTopics.SYNC_REQUESTED,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=json.loads,
        )
        await consumer.start()
        try:
            while True:
                message = await consumer.getone()
                try:
                    await self.process(message.value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception("review_sync_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        event_id = uuid.UUID(payload["event_id"])
        run_id = uuid.UUID(payload["run_id"])
        job_id = uuid.UUID(payload["job_id"])
        seller_id = uuid.UUID(payload["seller_id"])
        snapshot_date = date.fromisoformat(payload["snapshot_date"])

        # Phase 1: read everything needed and close the DB session before any HTTP request.
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            reviews = ReviewSyncRepository(session)
            if await sellers.inbox_processed(event_id):
                return
            job = await reviews.get_job(job_id)
            if job is None or job.status in {"success", "error"}:
                sellers.mark_inbox(event_id, "WBReviewSyncRequested")
                await session.commit()
                return
            seller = await sellers.get(seller_id)
            credential = await sellers.get_credential(seller_id)
            if seller is None or credential is None:
                await reviews.fail_job(job_id, "Селлер удалён или API-ключ отсутствует")
                await reviews.finalize_run(run_id)
                sellers.mark_inbox(event_id, "WBReviewSyncRequested")
                await session.commit()
                return
            await reviews.mark_job_running(job_id, self.lease_seconds)
            await reviews.mark_run_running(run_id)
            encrypted_key = credential.encrypted_api_key
            await session.commit()

        # Phase 2: talk to WB. The catalog is already in Postgres, so only
        # feedbacks are fetched here.
        try:
            api_key = self.cipher.decrypt(encrypted_key)
            aggregation = await self.feedback_client.aggregate(api_key)
        except (CredentialDecryptionError, WBFeedbackPermanentError) as error:
            await self._finish(event_id, run_id, job_id, str(error), retry=False)
            return
        except WBFeedbackTemporaryError as error:
            await self._finish(event_id, run_id, job_id, str(error), retry=True)
            return

        # Phase 3: persist the fully collected result in one short transaction.
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            reviews = ReviewSyncRepository(session)
            if await sellers.get(seller_id) is None:
                await reviews.fail_job(job_id, "Селлер удалён во время синхронизации")
            else:
                known = {article.article for article in await sellers.list_articles(seller_id)}
                unknown = self._unknown_cards(aggregation, known)
                await sellers.ensure_feedback_articles(seller_id, unknown)
                articles = known | {card.article for card in unknown}
                counts = {article: aggregation.counts.get(article, NO_RATINGS) for article in articles}
                await reviews.upsert_daily_counts(seller_id, snapshot_date, counts)
                await reviews.complete_job(job_id, len(counts), aggregation.feedback_count)
            await reviews.finalize_run(run_id)
            sellers.mark_inbox(event_id, "WBReviewSyncRequested")
            await session.commit()

    @staticmethod
    def _unknown_cards(aggregation: FeedbackAggregation, known: set[str]) -> list[CatalogCard]:
        return [
            CatalogCard(
                article=product.article,
                vendor_code=product.vendor_code,
                name=product.name,
                imt_id=product.imt_id,
            )
            for article, product in aggregation.products.items()
            if article not in known
        ]

    async def _finish(
        self,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        error: str,
        *,
        retry: bool,
    ) -> None:
        """Close out a failed attempt.

        The inbox row is always written and the offset always committed: a retry
        is re-dispatched later as a fresh event, which keeps redelivery bounded
        instead of blocking the partition on one throttled seller.
        """
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            reviews = ReviewSyncRepository(session)
            rescheduled = False
            if retry:
                rescheduled = await reviews.reschedule_job(
                    job_id,
                    error,
                    max_attempts=self.max_attempts,
                    backoff_seconds=self.retry_backoff_seconds,
                )
            if not rescheduled:
                await reviews.fail_job(job_id, error)
            await reviews.finalize_run(run_id)
            sellers.mark_inbox(event_id, "WBReviewSyncRequested")
            await session.commit()
        self.logger.warning(
            "review_sync_attempt_failed",
            extra={"job_id": str(job_id), "rescheduled": rescheduled, "error": error},
        )
