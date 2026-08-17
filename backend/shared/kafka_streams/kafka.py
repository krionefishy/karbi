import asyncio

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from fastapi import FastAPI

from backend.shared.kafka_streams.consumer import consume
from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.kafka_streams.subscribers import s3_consumers
from backend.shared.kafka_streams.topics import StorageTopics
from backend.storage.s3 import S3Client


async def ensure_topics(
    *,
    bootstrap_servers: str,
    partitions: int,
    replication_factor: int,
) -> None:
    """Create every registered application topic; the Python registry is the source of truth."""
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers)
    await admin.start()
    try:
        existing_topics = await admin.list_topics()
        missing_topics = [
            NewTopic(
                name=topic,
                num_partitions=partitions,
                replication_factor=replication_factor,
            )
            for topic in StorageTopics.all()
            if topic not in existing_topics
        ]
        if missing_topics:
            await admin.create_topics(missing_topics)
    finally:
        await admin.close()


async def start_kafka(
    *,
    app: FastAPI,
    bootstrap_servers: str,
    consumer_group: str,
    max_request_size: int,
    topic_partitions: int,
    topic_replication_factor: int,
    producer: KafkaProducerWrapper,
    s3_client: S3Client | None,
) -> None:
    await ensure_topics(
        bootstrap_servers=bootstrap_servers,
        partitions=topic_partitions,
        replication_factor=topic_replication_factor,
    )
    raw_producer = AIOKafkaProducer(
        bootstrap_servers=bootstrap_servers,
        enable_idempotence=True,
        max_request_size=max_request_size,
    )
    await raw_producer.start()
    producer.bind(raw_producer)
    app.state.kafka_producer_wrapper = producer
    app.state.kafka_producer = raw_producer
    app.state.kafka_consumer_tasks = []
    if s3_client is not None:
        app.state.kafka_consumer_tasks = [
            asyncio.create_task(
                consume(
                    bootstrap_servers=bootstrap_servers,
                    group_id=f"{consumer_group}.storage.s3",
                    topic=topic,
                    handler=handler,
                    s3_client=s3_client,
                    max_request_size=max_request_size,
                ),
                name=f"kafka:{topic}",
            )
            for topic, handler in s3_consumers
        ]


async def stop_kafka(app: FastAPI) -> None:
    tasks: list[asyncio.Task[None]] = getattr(app.state, "kafka_consumer_tasks", [])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    producer: AIOKafkaProducer | None = getattr(app.state, "kafka_producer", None)
    if producer is not None:
        await producer.stop()
    wrapper: KafkaProducerWrapper | None = getattr(app.state, "kafka_producer_wrapper", None)
    if wrapper is not None:
        wrapper.unbind()
