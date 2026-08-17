import json
from typing import Any

from aiokafka import AIOKafkaProducer

from backend.shared.kafka_streams.topics import validate_topic_name


class KafkaProducerWrapper:
    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    def bind(self, producer: AIOKafkaProducer) -> None:
        self._producer = producer

    async def send(self, topic: str, payload: dict[str, Any], *, key: str | None = None) -> None:
        if self._producer is None:
            raise RuntimeError("Kafka producer is not started")
        await self._producer.send_and_wait(
            validate_topic_name(topic),
            value=json.dumps(payload, separators=(",", ":")).encode(),
            key=key.encode() if key else None,
        )

    def unbind(self) -> None:
        self._producer = None
