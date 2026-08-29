import asyncio
import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBContentClient, WBPermanentError
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.storage.pg import Database
from backend.workers.wb_reviews.review_consumer import POLL_INTERVAL_MARGIN_MS, InvalidPayloadError

# A throttled catalog fetch legitimately outlives Kafka's default 300s poll
# interval; a rebalance in the middle of one just doubles the WB traffic.
DEFAULT_MAX_POLL_INTERVAL_MS = 1_800_000 + POLL_INTERVAL_MARGIN_MS


class CatalogSyncConsumer:
    def __init__(
        self,
        database: Database,
        bootstrap_servers: str,
        group_id: str,
        *,
        client: WBContentClient,
        max_poll_interval_ms: int = DEFAULT_MAX_POLL_INTERVAL_MS,
    ) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.client = client
        self.max_poll_interval_ms = max_poll_interval_ms
        self.logger = logging.getLogger("wb.catalog.consumer")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            WBCoreTopics.CATALOG_SYNC_REQUESTED,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=self.max_poll_interval_ms,
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
                    self.logger.exception("catalog_sync_payload_undecodable")
                    await consumer.commit()
                    continue
                try:
                    await self.process(payload)
                except asyncio.CancelledError:
                    raise
                except InvalidPayloadError:
                    self.logger.exception("catalog_sync_payload_invalid")
                except Exception:
                    self.logger.exception("catalog_sync_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        # A malformed message stays malformed forever: skip it, don't retry it.
        try:
            event_id = uuid.UUID(payload["event_id"])
            seller_id = uuid.UUID(payload["seller_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidPayloadError(str(error)) from error
        async with self.database.session() as session:
            repository = SellerRepository(session)
            if await repository.inbox_processed(event_id):
                return
            seller = await repository.get(seller_id)
            if seller is None:
                repository.mark_inbox(event_id, "WBCatalogSyncRequested")
                await session.commit()
                return
            await repository.set_sync_status(seller_id, "syncing")
            await session.commit()
        try:
            catalog = await self.client.get_catalog(str(seller_id))
        except WBPermanentError as error:
            async with self.database.session() as session:
                repository = SellerRepository(session)
                await repository.set_sync_status(seller_id, "error", str(error))
                repository.mark_inbox(event_id, "WBCatalogSyncRequested")
                await session.commit()
            return
        async with self.database.session() as session:
            repository = SellerRepository(session)
            if await repository.get(seller_id) is None:
                repository.mark_inbox(event_id, "WBCatalogSyncRequested")
                await session.commit()
                return
            await repository.upsert_catalog(
                seller_id,
                active=catalog.active,
                archived=catalog.archived,
                archived_available=catalog.archived_available,
            )
            await repository.set_sync_status(seller_id, "success")
            repository.mark_inbox(event_id, "WBCatalogSyncRequested")
            await session.commit()
        self.logger.info(
            "catalog_synced",
            extra={
                "seller_id": str(seller_id),
                "active": len(catalog.active),
                "archived": len(catalog.archived),
                "archived_available": catalog.archived_available,
            },
        )
