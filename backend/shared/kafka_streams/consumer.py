import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import TopicPartition

from backend.shared.kafka_streams.topics import validate_topic_name
from backend.storage.s3 import S3Client

ConsumerHandler = Callable[[S3Client, dict[str, Any]], Awaitable[None]]


def kafka_subscriber(topic: str) -> Callable[[ConsumerHandler], tuple[str, ConsumerHandler]]:
    validated_topic = validate_topic_name(topic)

    def decorate(handler: ConsumerHandler) -> tuple[str, ConsumerHandler]:
        return validated_topic, handler

    return decorate


async def consume(
    *,
    bootstrap_servers: str,
    group_id: str,
    topic: str,
    handler: ConsumerHandler,
    s3_client: S3Client,
    max_request_size: int,
) -> None:
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap_servers,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_partition_fetch_bytes=max_request_size,
        value_deserializer=json.loads,
    )
    await consumer.start()
    logger = logging.getLogger(f"kafka.consumer.{topic}")
    try:
        while True:
            message = await consumer.getone()
            partition = TopicPartition(message.topic, message.partition)
            try:
                await handler(s3_client, message.value)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Kafka message processing failed; offset will not be committed")
                consumer.seek(partition, message.offset)
                await asyncio.sleep(1)
                continue
            await consumer.commit({partition: message.offset + 1})
    finally:
        await consumer.stop()
