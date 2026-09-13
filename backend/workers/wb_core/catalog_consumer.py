import asyncio
import json
import logging
import uuid

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.application import MirrorService, SellerGoneError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.storage.pg import Database

# A throttled catalog fetch legitimately outlives Kafka's default 300s poll
# interval; a rebalance in the middle of one just doubles the WB traffic.
DEFAULT_MAX_POLL_INTERVAL_MS = 1_800_000 + 60_000
EVENT_TYPE = "WBCatalogSyncRequested"


class InvalidPayloadError(Exception):
    """The message can never be processed; retrying it would poison the partition."""


class CatalogSyncConsumer:
    """Внеочередной синк каталога по событию реестра: выдан ключ, нажата кнопка.

    Сам синк делает зеркало; консьюмер отвечает за идемпотентность события и
    за то, чтобы временная ошибка WB не стала пропущенным сообщением.
    """

    def __init__(
        self,
        database: Database,
        bootstrap_servers: str,
        group_id: str,
        *,
        mirror: MirrorService,
        max_poll_interval_ms: int = DEFAULT_MAX_POLL_INTERVAL_MS,
    ) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.mirror = mirror
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
            if await SellerRepository(session).inbox_processed(event_id):
                return
        try:
            await self.mirror.sync_catalog(seller_id)
        except SellerGoneError:
            # Событие, опубликованное до архивации, доживает в Kafka дольше
            # селлера: обрабатывать его — значит дёргать шлюз за отключённого.
            self.logger.info("catalog_sync_seller_gone", extra={"seller_id": str(seller_id)})
        except WBPermanentError:
            # Причина уже записана в статус синка; повтор того же события её не изменит.
            pass
        async with self.database.session() as session:
            SellerRepository(session).mark_inbox(event_id, EVENT_TYPE)
            await session.commit()
