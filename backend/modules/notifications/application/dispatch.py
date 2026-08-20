import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application.bots import BotNotFoundError, BotRegistry
from backend.modules.notifications.application.pacing import SendPacer
from backend.modules.notifications.application.templates import UnknownTemplateError, render
from backend.modules.notifications.domain import CHAT_AUDIENCE, MessageRequest
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import (
    TelegramClient,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramTemporaryError,
)
from backend.shared.security import CredentialDecryptionError

# Descriptions Telegram uses when the chat will never receive anything again.
DEAD_CHAT_MARKERS = ("bot was blocked", "chat not found", "bot was kicked", "user is deactivated")


@dataclass(frozen=True, slots=True)
class QueueResult:
    """What became of one notification event."""

    queued: int
    skipped: int
    rejected: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryReport:
    sent: int
    retried: int
    failed: int


class DispatchService:
    def __init__(
        self,
        session: AsyncSession,
        repository: NotificationRepository,
        bots: BotRegistry,
    ) -> None:
        self.session = session
        self.repository = repository
        self.bots = bots
        self.logger = logging.getLogger("notifications.dispatch")

    async def queue(self, request: MessageRequest) -> QueueResult:
        """Turn one event into one message per subscribed chat.

        A bot that does not exist or a template we cannot render is a producer
        bug, not a delivery failure: rejecting it keeps the partition moving
        instead of replaying a message nobody can ever send.
        """
        try:
            bot = await self.bots.by_code(request.bot_code)
        except BotNotFoundError:
            return QueueResult(0, 0, rejected=f"unknown or inactive bot: {request.bot_code}")
        try:
            text = render(request.template, request.params)
        except UnknownTemplateError:
            return QueueResult(0, 0, rejected=f"unknown template: {request.template}")

        if request.audience.kind == CHAT_AUDIENCE:
            chat_ids = [int(request.audience.chat_id or 0)]
        else:
            chat_ids = await self.repository.subscribers(bot.id, request.audience.seller_id)  # type: ignore[arg-type]

        queued = 0
        skipped = 0
        for chat_id in chat_ids:
            fresh = await self.repository.queue_message(
                bot_id=bot.id,
                chat_id=chat_id,
                # Per bot and per chat, so a digest reaching two chats — or one
                # chat through two bots — is not deduplicated into one, while a
                # replayed event still cannot double-send.
                dedupe_key=f"{bot.id}:{request.dedupe_key}:{chat_id}",
                template=request.template,
                params=request.params,
                text=text,
            )
            queued += int(fresh)
            skipped += int(not fresh)
        await self.session.commit()
        return QueueResult(queued, skipped)

    async def deliver_due(
        self,
        client: TelegramClient,
        *,
        max_attempts: int,
        backoff_seconds: int,
        limit: int = 50,
        bot_id: uuid.UUID | None = None,
        pacer: SendPacer | None = None,
    ) -> DeliveryReport:
        """Send what is due, for one bot when `bot_id` is given.

        A bot whose Telegram calls hang holds up nothing but its own queue: the
        supervisor runs one of these per bot, each with its own session.
        """
        sent = retried = failed = 0
        for message in await self.repository.due_messages(limit, bot_id=bot_id):
            try:
                token = await self.bots.token(message.bot_id)
            except (BotNotFoundError, CredentialDecryptionError) as error:
                # One undecryptable token must not stall every other bot's queue.
                await self.repository.mark_failed(message.id, str(error))
                failed += 1
                await self.session.commit()
                continue
            if pacer is not None:
                # Waiting before the call, not reacting to 429 after it: a 429
                # costs the whole queue a stall, and Telegram counts it against
                # us either way.
                await pacer.wait_turn(message.chat_id)
            try:
                telegram_message_id = await client.send(token, message.chat_id, message.text)
            except TelegramPermanentError as error:
                # Blocked bot, deleted chat: another attempt changes nothing.
                await self.repository.mark_failed(message.id, str(error))
                if any(marker in str(error).lower() for marker in DEAD_CHAT_MARKERS):
                    await self.repository.unsubscribe_chat(message.bot_id, message.chat_id)
                failed += 1
            except TelegramRateLimitError as error:
                await self.repository.mark_rate_limited(
                    message.id, str(error), retry_after_seconds=error.retry_after or backoff_seconds
                )
                retried += 1
            except TelegramTemporaryError as error:
                if await self.repository.mark_retry(
                    message.id, str(error), max_attempts=max_attempts, backoff_seconds=backoff_seconds
                ):
                    retried += 1
                else:
                    failed += 1
            else:
                await self.repository.mark_sent(message.id, telegram_message_id)
                sent += 1
            # One commit per message: a crash mid-batch can then duplicate at
            # most the message in flight, not the whole batch.
            await self.session.commit()
        return DeliveryReport(sent, retried, failed)
