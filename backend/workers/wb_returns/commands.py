"""Консьюмер команд бота возвратов из Kafka."""

import asyncio
import json
import logging
from zoneinfo import ZoneInfo

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.notifications.domain import CommandEvent
from backend.modules.wb_returns.application import CommandService
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.settings import Settings
from backend.storage.pg import Database
from backend.workers.wb_returns.worker import extension_service


class CommandConsumer:
    """Читает команды всех ботов, отвечает только за свой: чужие пропускает и коммитит."""

    def __init__(
        self,
        database: Database,
        bootstrap_servers: str,
        group_id: str,
        *,
        settings: Settings,
    ) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.settings = settings
        self.bot_code = settings.returns.notification_bot
        self.timezone = ZoneInfo(settings.returns.timezone)
        self.logger = logging.getLogger("wb.returns.commands.consumer")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            NotificationTopics.TELEGRAM_COMMAND_RECEIVED,
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
                    self.logger.exception("returns_command_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, raw: bytes) -> str | None:
        try:
            event = CommandEvent.parse(json.loads(raw))
        except Exception as error:
            # Кривой payload останется кривым: повтор его не починит, партицию не держим.
            self.logger.error("returns_command_rejected", extra={"error": str(error)})
            return None
        if event.bot_code != self.bot_code:
            return None
        async with self.database.session() as session:
            service = CommandService(
                session,
                ReturnsRepository(session),
                extension_service(session, self.settings),
                bot_code=self.bot_code,
                timezone=self.timezone,
            )
            template = await service.handle(event)
        self.logger.info(
            "returns_command_answered",
            extra={"chat_id": event.chat_id, "command": event.command, "template": template},
        )
        return template
