import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_screenshot_control.application.inbox import InvalidEventError

Handler = Callable[[dict[str, Any]], Awaitable[None]]
PoisonHandler = Callable[[dict[str, Any], str], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]
# Распознавание с повтором ремонтного вызова укладывается в пару минут; запас, чтобы группа не перебалансировалась.
MAX_POLL_INTERVAL_MS = 600_000
MAX_ATTEMPTS = 5
"""Сколько раз подряд обработчик может упасть на одном сообщении, прежде чем оно будет отложено."""
RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0)


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:500]


class EventConsumer:
    """Один топик — одна группа. Оффсет коммитится после обработчика; ошибка — повтор того же сообщения.

    Неразбираемое сообщение пропускается с записью в лог: повтор его не починит, а партицию заблокирует.
    Сообщение, на котором обработчик падает `max_attempts` раз подряд («ядовитое»: баг, нарушение
    ограничения БД), тоже не держит партицию: отказ пишется в лог с ошибкой, `on_poison` переводит
    затронутый скрин на ручную проверку, оффсет коммитится. Иначе одно сообщение `extracted` (все они
    идут одним ключом) остановило бы решения по всем менеджерам.
    """

    def __init__(
        self,
        *,
        topic: str,
        bootstrap_servers: str,
        group_id: str,
        handler: Handler,
        on_poison: PoisonHandler | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.handler = handler
        self.on_poison = on_poison
        self.max_attempts = max_attempts
        self.sleep = sleep
        self.failures: dict[str, int] = {}
        self.logger = structlog.get_logger(f"wb.screenshot_control.consumer.{topic}")

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
                if not await self.handle(message.value, position=f"{message.partition}:{message.offset}"):
                    consumer.seek(partition, message.offset)
                    continue
                await consumer.commit({partition: message.offset + 1})
        finally:
            await consumer.stop()

    async def handle(self, raw: bytes | None, *, position: str) -> bool:
        """`True` — оффсет можно коммитить (обработано, пропущено или отложено как ядовитое)."""
        key = self._key(raw, position)
        try:
            await self.process(raw)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            attempts = self.failures.get(key, 0) + 1
            if attempts < self.max_attempts:
                self.failures[key] = attempts
                self.logger.exception("screenshot_event_failed", key=key, attempt=attempts)
                await self.sleep(RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)])
                return False
            self.failures.pop(key, None)
            await self._park(raw, key, attempts, _error(error))
            return True
        self.failures.pop(key, None)
        return True

    async def process(self, raw: bytes | None) -> None:
        payload = self._payload(raw)
        try:
            if payload is None:
                raise InvalidEventError("payload is not a JSON object")
            await self.handler(payload)
        except InvalidEventError as error:
            self.logger.error("screenshot_event_rejected", error=str(error)[:500])

    async def _park(self, raw: bytes | None, key: str, attempts: int, error: str) -> None:
        self.logger.error("screenshot_event_parked", topic=self.topic, key=key, attempts=attempts, error=error)
        payload = self._payload(raw)
        if self.on_poison is None or payload is None:
            return
        try:
            await self.on_poison(payload, error)
        except Exception:
            # Отметить скрин не вышло (например, база недоступна): сообщение всё равно не держим —
            # незавершённый скрин подберёт сверка конвейера или админ по экрану «Конвейер».
            self.logger.exception("screenshot_event_park_failed", topic=self.topic, key=key)

    @staticmethod
    def _payload(raw: bytes | None) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw or b"")
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _key(cls, raw: bytes | None, position: str) -> str:
        """Попытки считаются по `event_id`, а без него — по позиции в партиции."""
        payload = cls._payload(raw)
        event_id = payload.get("event_id") if payload else None
        return f"event:{event_id}" if event_id else f"offset:{position}"
