import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application.bots import BotNotFoundError, BotRegistry
from backend.modules.notifications.application.templates import UnknownTemplateError, render
from backend.modules.notifications.domain import CHAT_AUDIENCE, MessageRequest
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import (
    TelegramClient,
    TelegramPermanentError,
    TelegramTemporaryError,
)


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
                # Per chat, so a digest reaching two chats is not deduplicated
                # into one, while a replayed event still cannot double-send.
                dedupe_key=f"{request.dedupe_key}:{chat_id}",
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
    ) -> DeliveryReport:
        sent = retried = failed = 0
        for message in await self.repository.due_messages(limit):
            token = await self.bots.token(message.bot_id)
            try:
                telegram_message_id = await client.send(token, message.chat_id, message.text)
            except TelegramPermanentError as error:
                # Blocked bot, deleted chat: another attempt changes nothing.
                await self.repository.mark_failed(message.id, str(error))
                failed += 1
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
        await self.session.commit()
        return DeliveryReport(sent, retried, failed)
