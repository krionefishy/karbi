import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.notifications.application import BotRegistry, DispatchService, SendPacer
from backend.modules.notifications.application.pacing import (
    DEFAULT_BOT_MESSAGES_PER_SECOND,
    DEFAULT_CHAT_MESSAGES_PER_SECOND,
)
from backend.modules.notifications.domain import Bot, MessageRequest
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import TelegramClient
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.security import CredentialCipher
from backend.storage.pg import Database


class NotificationSender:
    """Kafka in, rows in `outgoing_messages` out; delivery is a separate loop.

    Splitting the two means a Telegram outage never blocks the partition, and a
    committed offset always means "we owe these chats a message", not "sent".
    Delivery then runs one loop per bot, so a slow bot costs only its own queue.
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
        bot_messages_per_second: float = DEFAULT_BOT_MESSAGES_PER_SECOND,
        chat_messages_per_second: float = DEFAULT_CHAT_MESSAGES_PER_SECOND,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.client = client
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.delivery_interval_seconds = delivery_interval_seconds
        self.send_max_attempts = send_max_attempts
        self.send_retry_backoff_seconds = send_retry_backoff_seconds
        self.bot_messages_per_second = bot_messages_per_second
        self.chat_messages_per_second = chat_messages_per_second
        self.logger = logging.getLogger("notifications.sender")

    async def consume(self) -> None:
        consumer = AIOKafkaConsumer(
            NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
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

    async def process(self, raw: bytes) -> None:
        try:
            request = MessageRequest.parse(json.loads(raw))
        except Exception as error:
            # Parsing touches nothing but the payload, so any failure here — bad
            # JSON, a JSON array, a missing field — can only ever repeat itself:
            # replaying it forever would wedge the partition behind a producer bug.
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

    async def deliver_forever(self, bot: Bot) -> None:
        """One bot's delivery loop; the supervisor runs one of these per bot.

        A bot whose Telegram calls hang delays only its own queue — the reason
        this is a task per bot rather than one loop over everything.
        """
        pacer = SendPacer(
            bot_messages_per_second=self.bot_messages_per_second,
            chat_messages_per_second=self.chat_messages_per_second,
        )
        while True:
            try:
                await self.deliver_once(bot, pacer)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("notification_delivery_failed", extra={"bot": bot.code})
            await asyncio.sleep(self.delivery_interval_seconds)

    async def deliver_once(self, bot: Bot, pacer: SendPacer | None = None) -> None:
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            dispatch = DispatchService(session, repository, BotRegistry(session, repository, self.cipher))
            report = await dispatch.deliver_due(
                self.client,
                max_attempts=self.send_max_attempts,
                backoff_seconds=self.send_retry_backoff_seconds,
                bot_id=bot.id,
                pacer=pacer,
            )
        if report.sent or report.retried or report.failed:
            self.logger.info(
                "notification_delivery",
                extra={
                    "bot": bot.code,
                    "sent": report.sent,
                    "retried": report.retried,
                    "failed": report.failed,
                },
            )
