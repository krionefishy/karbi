import asyncio
import json
import logging
import uuid
from datetime import date

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBContentClient, WBPermanentError, WBTemporaryError
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.modules.wb_reviews.infrastructure.wb import (
    WBFeedbackClient,
    WBFeedbackPermanentError,
    WBFeedbackTemporaryError,
)
from backend.shared.kafka_streams.topics import WBReviewsTopics
from backend.shared.security import CredentialCipher, CredentialDecryptionError
from backend.storage.pg import Database


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
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.catalog_client = WBContentClient()
        self.feedback_client = WBFeedbackClient(
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
            await reviews.mark_job_running(job_id)
            await reviews.mark_run_running(run_id)
            encrypted_key = credential.encrypted_api_key
            await session.commit()

        try:
            api_key = self.cipher.decrypt(encrypted_key)
            catalog = await self.catalog_client.get_articles(api_key)
            aggregation = await self.feedback_client.aggregate(api_key)
        except (
            CredentialDecryptionError,
            WBPermanentError,
            WBTemporaryError,
            WBFeedbackPermanentError,
            WBFeedbackTemporaryError,
        ) as error:
            await self._finish_failure(event_id, run_id, job_id, str(error))
            return

        products = {item["article"]: item for item in catalog}
        for article, product in aggregation.products.items():
            products.setdefault(
                article,
                {"article": article, "vendor_code": product.vendor_code, "name": product.name},
            )
        article_counts = {article: aggregation.counts.get(article, (0, 0, 0, 0, 0)) for article in products}

        # Phase 3: persist the fully collected result in one short transaction.
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            reviews = ReviewSyncRepository(session)
            if await sellers.get(seller_id) is None:
                await reviews.fail_job(job_id, "Селлер удалён во время синхронизации")
            else:
                await sellers.upsert_articles(seller_id, list(products.values()))
                await reviews.upsert_daily_counts(seller_id, snapshot_date, article_counts)
                await reviews.complete_job(job_id, len(products), aggregation.feedback_count)
            await reviews.finalize_run(run_id)
            sellers.mark_inbox(event_id, "WBReviewSyncRequested")
            await session.commit()

    async def _finish_failure(
        self,
        event_id: uuid.UUID,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        error: str,
    ) -> None:
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            reviews = ReviewSyncRepository(session)
            await reviews.fail_job(job_id, error)
            await reviews.finalize_run(run_id)
            sellers.mark_inbox(event_id, "WBReviewSyncRequested")
            await session.commit()
