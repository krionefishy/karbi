import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_screenshot_control.application.inbox import InvalidEventError

Handler = Callable[[dict[str, Any]], Awaitable[None]]
# Распознавание с повтором ремонтного вызова укладывается в пару минут; запас, чтобы группа не перебалансировалась.
MAX_POLL_INTERVAL_MS = 600_000


class EventConsumer:
    """Один топик — одна группа. Оффсет коммитится после обработчика; ошибка — повтор того же сообщения.

    Неразбираемое сообщение пропускается с записью в лог: повтор его не починит, а партицию заблокирует.
    """

    def __init__(self, *, topic: str, bootstrap_servers: str, group_id: str, handler: Handler) -> None:
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.handler = handler
        self.logger = logging.getLogger(f"wb.screenshot_control.consumer.{topic}")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=MAX_POLL_INTERVAL_MS,
        )
        await consumer.start()
        try:
            while True:
                message = await consumer.getone()
                partition = TopicPartition(message.topic, message.partition)
                try:
                    await self.process(message.value)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception("screenshot_event_failed")
                    consumer.seek(partition, message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit({partition: message.offset + 1})
        finally:
            await consumer.stop()

    async def process(self, raw: bytes | None) -> None:
        try:
            payload = json.loads(raw or b"")
        except ValueError:
            payload = None
        try:
            if not isinstance(payload, dict):
                raise InvalidEventError("payload is not a JSON object")
            await self.handler(payload)
        except InvalidEventError as error:
            self.logger.error("screenshot_event_rejected", extra={"error": str(error)[:500]})
