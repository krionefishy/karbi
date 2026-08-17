import asyncio
import logging
import uuid
from contextlib import suppress

from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.outbox.repository import OutboxRepository
from backend.storage.pg import Database


class OutboxPublisher:
    def __init__(self, database: Database, producer: KafkaProducerWrapper, poll_seconds: float = 1.0) -> None:
        self.database = database
        self.producer = producer
        self.poll_seconds = poll_seconds
        self.worker_id = f"outbox-{uuid.uuid4()}"
        self.logger = logging.getLogger("outbox.publisher")
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            async with self.database.session() as session:
                events = await OutboxRepository(session).claim(self.worker_id)
            for event in events:
                try:
                    await self.producer.send(event.topic, event.payload, key=event.message_key)
                except Exception as error:
                    self.logger.exception("outbox_publish_failed", extra={"event_id": str(event.id)})
                    async with self.database.session() as session:
                        await OutboxRepository(session).failed(event.id, type(error).__name__, event.attempts)
                else:
                    async with self.database.session() as session:
                        await OutboxRepository(session).published(event.id)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
