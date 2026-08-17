import asyncio
import signal

from aiokafka import AIOKafkaProducer

from backend.infrastructure.logging import configure_logging
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.outbox import OutboxPublisher
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def main() -> None:
    settings = load_settings()
    settings.validate_runtime_secrets()
    if not settings.kafka.enabled:
        raise RuntimeError("Outbox publisher requires Kafka to be enabled")
    configure_logging(settings.app.log_level)
    database = Database()
    wrapper = KafkaProducerWrapper()
    await database.connect(settings.database.url, pool_size=2, max_overflow=1)
    await ensure_topics(
        bootstrap_servers=settings.kafka.bootstrap_servers,
        partitions=settings.kafka.topic_partitions,
        replication_factor=settings.kafka.topic_replication_factor,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka.bootstrap_servers,
        enable_idempotence=True,
        max_request_size=settings.kafka.max_request_size,
    )
    await producer.start()
    wrapper.bind(producer)
    publisher = OutboxPublisher(database, wrapper)
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, publisher.stop)
    try:
        await publisher.run()
    finally:
        await producer.stop()
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
