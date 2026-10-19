import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_support_bot.domain import SYSTEM_PROMPT, parse_verdict, user_message
from backend.modules.wb_support_bot.infrastructure.postgres import (
    VERDICT_DONE,
    VERDICT_FAILED,
    VERDICT_PENDING,
    SupportBotRepository,
)
from backend.shared.kafka_streams.topics import SupportBotTopics
from backend.shared.llm import LLMClient, LLMError, mask_pii
from backend.storage.pg import Database

EVENT_TYPE = "SupportBotClassifyRequested"
POLL_INTERVAL_MARGIN_MS = 60_000
POLL_INTERVAL_BASE_MS = 300_000


class InvalidPayloadError(Exception):
    """Сообщение не разобрать никогда: повтор только заблокировал бы партицию."""


class ClassifyConsumer:
    """Читает запросы вердикта из Kafka и спрашивает модель. Результат — строка в `verdicts`.

    Бот поддержки сюда не заходит и ответа не ждёт: запрос лежит в outbox, а вердикт
    появляется позже. Упавший запрос бот сам поставит заново на следующем проходе.
    """

    def __init__(
        self,
        database: Database,
        bootstrap_servers: str,
        group_id: str,
        llm: LLMClient,
    ) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.llm = llm
        self.logger = logging.getLogger("wb.support_bot.classify")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            SupportBotTopics.CLASSIFY_REQUESTED,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=POLL_INTERVAL_BASE_MS + POLL_INTERVAL_MARGIN_MS,
        )
        await consumer.start()
        try:
            while True:
                message = await consumer.getone()
                try:
                    payload = json.loads(message.value)
                except (TypeError, ValueError):
                    self.logger.exception("classify_payload_undecodable")
                    await consumer.commit()
                    continue
                try:
                    await self.process(payload)
                except asyncio.CancelledError:
                    raise
                except InvalidPayloadError:
                    self.logger.exception("classify_payload_invalid")
                except Exception:
                    self.logger.exception("classify_message_failed")
                    consumer.seek(TopicPartition(message.topic, message.partition), message.offset)
                    await asyncio.sleep(1)
                    continue
                await consumer.commit()
        finally:
            await consumer.stop()

    async def process(self, payload: dict) -> None:
        try:
            event_id = uuid.UUID(payload["event_id"])
            verdict_id = uuid.UUID(payload["verdict_id"])
        except (KeyError, TypeError, ValueError) as bad:
            raise InvalidPayloadError(str(bad)) from bad

        async with self.database.session() as session:
            sellers = SellerRepository(session)
            if await sellers.inbox_processed(event_id):
                return
            repository = SupportBotRepository(session)
            row = await repository.get_verdict(verdict_id)
            if row is None or row.status == VERDICT_DONE:
                sellers.mark_inbox(event_id, EVENT_TYPE)
                await session.commit()
                return
            seller_id, tail_event_id = row.seller_id, row.tail_event_id
            event = await ChatMirror(session).event(seller_id, tail_event_id)
        text = (event.text or "") if event is not None else None

        result = None
        error: str | None = None
        if text is None:
            error = "сообщения нет в зеркале чатов"
        else:
            try:
                result = await self.llm.complete(SYSTEM_PROMPT, user_message(mask_pii(text)))
            except LLMError as failure:
                error = str(failure)

        async with self.database.session() as session:
            sellers = SellerRepository(session)
            if await sellers.inbox_processed(event_id):
                return
            row = await SupportBotRepository(session).get_verdict(verdict_id)
            if row is not None and row.status != VERDICT_DONE:
                if result is not None:
                    verdict, reason = parse_verdict(result.text)
                    row.status = VERDICT_DONE
                    row.verdict = verdict
                    row.reason = reason
                    row.model = result.model
                    row.tokens_in = result.tokens_in
                    row.tokens_out = result.tokens_out
                    row.latency_ms = result.latency_ms
                    row.error = None
                else:
                    row.status = VERDICT_FAILED
                    row.error = (error or "неизвестная ошибка")[:500]
                row.completed_at = datetime.now(UTC)
            sellers.mark_inbox(event_id, EVENT_TYPE)
            await session.commit()
        if error is not None:
            self.logger.warning("classify_failed", extra={"verdict_id": str(verdict_id), "error": error})


__all__ = ["ClassifyConsumer", "VERDICT_PENDING"]
