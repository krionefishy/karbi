import json
from typing import Any

from aiokafka import AIOKafkaProducer


class KafkaProducer:
    def __init__(self, bootstrap_servers: str) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            enable_idempotence=True,
            value_serializer=lambda value: json.dumps(value).encode(),
        )

    async def connect(self) -> None:
        await self._producer.start()

    async def disconnect(self) -> None:
        await self._producer.stop()

    async def publish(self, topic: str, payload: dict[str, Any], key: str | None = None) -> None:
        await self._producer.send_and_wait(
            topic,
            value=payload,
            key=key.encode() if key else None,
        )
