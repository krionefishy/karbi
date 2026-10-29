import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from aiokafka import AIOKafkaConsumer, TopicPartition

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, ChatEvent
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_support_bot.domain import (
    REPLY_SYSTEM_PROMPT,
    is_wb_notice,
    parse_reply_verdict,
    reply_user_message,
)
from backend.modules.wb_support_bot.domain.reply import CONTEXT_MESSAGES
from backend.modules.wb_support_bot.infrastructure.postgres import (
    VERDICT_DONE,
    VERDICT_FAILED,
    DialogKey,
    SupportBotRepository,
)
from backend.shared.kafka_streams.topics import SupportBotTopics
from backend.shared.llm import LLMClient, LLMError, mask_pii
from backend.storage.pg import Database

EVENT_TYPE = "SupportBotReplyRequested"
POLL_INTERVAL_MARGIN_MS = 60_000
POLL_INTERVAL_BASE_MS = 300_000
CONTEXT_LOOKBACK = timedelta(days=14)
ROLE_BUYER = "покупатель"
ROLE_SELLER = "продавец"
ROLE_BOT = "напоминание бота"


class InvalidPayloadError(Exception):
    """Сообщение не разобрать никогда: повтор только заблокировал бы партицию."""


class ReplyConsumer:
    """Читает запросы оценки ответа из Kafka и спрашивает модель. Результат — класс в `reply_verdicts`.

    Бот поддержки ответа не ждёт: пока класса нет, диалог удержан. Упавший запрос бот
    сам поставит заново на следующем проходе, не больше `reply_max_attempts` раз.
    """

    def __init__(self, database: Database, bootstrap_servers: str, group_id: str, llm: LLMClient) -> None:
        self.database = database
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.llm = llm
        self.logger = logging.getLogger("wb.support_bot.reply")

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            SupportBotTopics.REPLY_REQUESTED,
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
                    self.logger.exception("reply_payload_undecodable")
                    await consumer.commit()
                    continue
                try:
                    await self.process(payload)
                except asyncio.CancelledError:
                    raise
                except InvalidPayloadError:
                    self.logger.exception("reply_payload_invalid")
                except Exception:
                    self.logger.exception("reply_message_failed")
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
            row = await repository.get_reply_verdict(verdict_id)
            if row is None or row.status == VERDICT_DONE:
                sellers.mark_inbox(event_id, EVENT_TYPE)
                await session.commit()
                return
            seller_id, chat_id = row.seller_id, row.chat_id
            first_at, last_at, prompt_at = row.first_event_at, row.last_event_at, row.prompt_at
            events = await ChatMirror(session).chat_events(
                seller_id, chat_id, since=first_at - CONTEXT_LOOKBACK, until=last_at
            )
            reminders = {
                send.text.strip() for send in await repository.dialog_sends(DialogKey(seller_id, chat_id, prompt_at))
            }
        messages, context = self._split(events, first_at, last_at, reminders)

        result = None
        error: str | None = None
        if not messages:
            error = "сообщений среза нет в зеркале чатов"
        else:
            masked_context = [(role, mask_pii(text)) for role, text in context]
            masked = [mask_pii(text) for text in messages]
            try:
                result = await self.llm.complete(REPLY_SYSTEM_PROMPT, reply_user_message(masked_context, masked))
            except LLMError as failure:
                error = str(failure)

        async with self.database.session() as session:
            sellers = SellerRepository(session)
            if await sellers.inbox_processed(event_id):
                return
            row = await SupportBotRepository(session).get_reply_verdict(verdict_id)
            if row is not None and row.status != VERDICT_DONE:
                if result is not None:
                    category, reason = parse_reply_verdict(result.text)
                    row.status = VERDICT_DONE
                    row.category = category
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
            self.logger.warning("reply_classify_failed", extra={"verdict_id": str(verdict_id), "error": error})

    @staticmethod
    def _split(
        events: list[ChatEvent], first_at: datetime, last_at: datetime, reminders: set[str]
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Тексты среза и до четырёх предыдущих сообщений диалога с ролями."""
        visible = [event for event in events if not is_wb_notice(event) and not event.review_prompt]
        messages = [
            event.text or ""
            for event in visible
            if event.sender == CHAT_SENDER_CLIENT and first_at <= event.added_at <= last_at
        ]
        before = [event for event in visible if event.added_at < first_at][-CONTEXT_MESSAGES:]
        context: list[tuple[str, str]] = []
        for event in before:
            text = (event.text or "").strip()
            if event.sender == CHAT_SENDER_CLIENT:
                role = ROLE_BUYER
            elif event.sender == CHAT_SENDER_SELLER and text in reminders:
                role = ROLE_BOT
            else:
                role = ROLE_SELLER
            context.append((role, text))
        return messages, context


__all__ = ["ReplyConsumer"]
