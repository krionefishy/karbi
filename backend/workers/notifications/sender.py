import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.notifications.application import BotRegistry, DispatchService
from backend.modules.notifications.domain import MessageRequest
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import TelegramClient
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.security import CredentialCipher
from backend.storage.pg import Database


class NotificationSender:
    """Kafka in, rows in `outgoing_messages` out; delivery is a separate loop.

    Splitting the two means a Telegram outage never blocks the partition, and a
    committed offset always means "we owe these chats a message", not "sent".
    """

    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher,
        client: TelegramClient,
        bootstrap_servers: str,
        group_id: str,
        *,
        delivery_interval_seconds: float,
        send_max_attempts: int,
        send_retry_backoff_seconds: int,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.client = client
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.delivery_interval_seconds = delivery_interval_seconds
        self.send_max_attempts = send_max_attempts
        self.send_retry_backoff_seconds = send_retry_backoff_seconds
        self.logger = logging.getLogger("notifications.sender")

    async def consume(self) -> None:
        consumer = AIOKafkaConsumer(
            NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
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
                    self.logger.exception("notification_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        try:
            request = MessageRequest.parse(payload)
        except (ValueError, KeyError, TypeError) as error:
            # A malformed event can only ever be malformed: replaying it forever
            # would wedge the partition behind a producer bug.
            self.logger.error("notification_payload_rejected", extra={"error": str(error)})
            return
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            dispatch = DispatchService(session, repository, BotRegistry(session, repository, self.cipher))
            result = await dispatch.queue(request)
        if result.rejected:
            self.logger.error(
                "notification_rejected", extra={"message_id": request.message_id, "reason": result.rejected}
            )
            return
        self.logger.info(
            "notification_queued",
            extra={"message_id": request.message_id, "queued": result.queued, "skipped": result.skipped},
        )

    async def deliver_forever(self) -> None:
        while True:
            try:
                await self.deliver_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("notification_delivery_failed")
            await asyncio.sleep(self.delivery_interval_seconds)

    async def deliver_once(self) -> None:
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            dispatch = DispatchService(session, repository, BotRegistry(session, repository, self.cipher))
            report = await dispatch.deliver_due(
                self.client,
                max_attempts=self.send_max_attempts,
                backoff_seconds=self.send_retry_backoff_seconds,
            )
        if report.sent or report.retried or report.failed:
            self.logger.info(
                "notification_delivery",
                extra={"sent": report.sent, "retried": report.retried, "failed": report.failed},
            )
