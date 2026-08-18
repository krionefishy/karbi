import asyncio
import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBContentClient, WBPermanentError
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.shared.security import CredentialCipher
from backend.storage.pg import Database


class CatalogSyncConsumer:
    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher,
        bootstrap_servers: str,
        group_id: str,
        client: WBContentClient | None = None,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.client = client or WBContentClient()
        self.logger = logging.getLogger("wb.catalog.consumer")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            WBCoreTopics.CATALOG_SYNC_REQUESTED,
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
                    self.logger.exception("catalog_sync_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        event_id = uuid.UUID(payload["event_id"])
        seller_id = uuid.UUID(payload["seller_id"])
        async with self.database.session() as session:
            repository = SellerRepository(session)
            if await repository.inbox_processed(event_id):
                return
            seller = await repository.get(seller_id)
            credential = await repository.get_credential(seller_id)
            if seller is None or credential is None:
                repository.mark_inbox(event_id, "WBCatalogSyncRequested")
                await session.commit()
                return
            await repository.set_sync_status(seller_id, "syncing")
            await session.commit()
            encrypted_key = credential.encrypted_api_key
        try:
            catalog = await self.client.get_catalog(self.cipher.decrypt(encrypted_key))
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
