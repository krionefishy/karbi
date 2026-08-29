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
from backend.storage.pg import Database

NO_RATINGS = (0, 0, 0, 0, 0)
# Extra time on top of the job lease before Kafka considers the consumer dead.
# The default 300s poll interval is shorter than a legitimate job, and a
# rebalance in the middle of one means double work and a dead task.
POLL_INTERVAL_MARGIN_MS = 60_000


class InvalidPayloadError(Exception):
    """The message can never be processed; retrying it would poison the partition."""


class ReviewSyncConsumer:
    def __init__(
        self,
        database: Database,
        bootstrap_servers: str,
        group_id: str,
        page_size: int,
        *,
        lease_seconds: int = 1800,
        max_attempts: int = 3,
        retry_backoff_seconds: int = 300,
        client: WBFeedbackClient,
    ) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.feedback_client = client
        self.logger = logging.getLogger("wb.reviews.consumer")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            WBReviewsTopics.SYNC_REQUESTED,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=self.lease_seconds * 1000 + POLL_INTERVAL_MARGIN_MS,
        )
        await consumer.start()
        try:
            while True:
                message = await consumer.getone()
                # Decoded here rather than by a value_deserializer: a deserializer
                # raises inside getone(), outside every guard below, which kills
                # the task and leaves the restart re-reading the same message.
                try:
                    payload = json.loads(message.value)
                except (TypeError, ValueError):
                    self.logger.exception("review_sync_payload_undecodable")
                    await consumer.commit()
                    continue
                try:
                    await self.process(payload)
                except asyncio.CancelledError:
                    raise
                except InvalidPayloadError:
                    self.logger.exception("review_sync_payload_invalid")
                except Exception:
                    self.logger.exception("review_sync_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        # Parse before touching the database: a malformed message stays
        # malformed forever, so it is skipped instead of retried.
        try:
            event_id = uuid.UUID(payload["event_id"])
            run_id = uuid.UUID(payload["run_id"])
            job_id = uuid.UUID(payload["job_id"])
            seller_id = uuid.UUID(payload["seller_id"])
            snapshot_date = date.fromisoformat(payload["snapshot_date"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidPayloadError(str(error)) from error

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
            if seller is None or seller.archived_at is not None:
                await reviews.fail_job(job_id, "Селлер удалён или в архиве")
                await reviews.finalize_run(run_id)
                sellers.mark_inbox(event_id, "WBReviewSyncRequested")
                await session.commit()
                return
            if not await reviews.mark_job_running(job_id, self.lease_seconds):
                # Someone else holds the job (or the reaper already closed it);
                # going to WB anyway would double the work and the traffic.
                sellers.mark_inbox(event_id, "WBReviewSyncRequested")
                await session.commit()
                self.logger.info("review_sync_job_not_claimed", extra={"job_id": str(job_id)})
                return
            await reviews.mark_run_running(run_id)
            await session.commit()

        # Phase 2: talk to WB. The catalog is already in Postgres, so only
        # feedbacks are fetched here.
        try:
            aggregation = await self.feedback_client.aggregate(str(seller_id))
        except WBFeedbackPermanentError as error:
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
            elif not await reviews.is_tracked(seller_id):
                # Detached from the automation while the job was in flight: his
                # history was just purged, so writing counts would resurrect it.
                await reviews.complete_job(job_id, 0, 0)
                self.logger.info(
                    "review_sync_seller_detached_skipped",
                    extra={"job_id": str(job_id), "seller_id": str(seller_id)},
                )
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
