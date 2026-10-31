import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application.bots import BotNotFoundError, BotRegistry
from backend.modules.notifications.application.pacing import SendPacer
from backend.modules.notifications.application.templates import UnknownTemplateError, render, render_attachment
from backend.modules.notifications.domain import (
    CALLBACK_ANSWER_KIND,
    CHAT_AUDIENCE,
    EDIT_KIND,
    MessageRequest,
    MessengerPermanentError,
    MessengerRateLimitError,
    MessengerTemporaryError,
    buttons_payload,
    parse_buttons,
)
from backend.modules.notifications.infrastructure.postgres import NotificationRepository, OutgoingMessageModel
from backend.modules.notifications.infrastructure.relay import RelayClient

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
            attachment = render_attachment(request.template, request.params)
        except UnknownTemplateError:
            return QueueResult(0, 0, rejected=f"unknown template: {request.template}")
        # Кнопки и ответ-цитата приходят в params шаблона; отправит их только интерактивный релей.
        try:
            buttons = buttons_payload(parse_buttons(request.params.get("buttons")))
        except ValueError as error:
            return QueueResult(0, 0, rejected=f"bad buttons: {error}")
        # Контракт релея: `reply_to` — номер сообщения цифрами; иное релей отверг бы вместе с сообщением.
        raw_reply = str(request.params.get("reply_to") or "")
        reply_to = raw_reply if raw_reply.isdigit() and len(raw_reply) <= 20 else None

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
                dedupe_key=f"{bot.id}:{request.dedupe_key}:{chat_id}",
                template=request.template,
                params=request.params,
                text=text,
                attachment=attachment,
                kind=request.kind,
                buttons=buttons,
                reply_to=reply_to,
                target_ref=request.target_ref,
            )
            queued += int(fresh)
            skipped += int(not fresh)
        await self.session.commit()
        return QueueResult(queued, skipped)

    async def deliver_due(
        self,
        client: RelayClient,
        *,
        max_attempts: int,
        backoff_seconds: int,
        limit: int = 50,
        bot_id: uuid.UUID | None = None,
        pacer: SendPacer | None = None,
    ) -> DeliveryReport:
        """Send what is due, for one bot when `bot_id` is given.

        A bot whose relay calls hang holds up nothing but its own queue: the
        supervisor runs one of these per bot, each with its own session.
        """
        sent = retried = failed = 0
        codes: dict[uuid.UUID, str] = {}
        for message in await self.repository.due_messages(limit, bot_id=bot_id):
            try:
                if message.bot_id not in codes:
                    codes[message.bot_id] = (await self.bots.by_id(message.bot_id)).code
            except BotNotFoundError as error:
                await self.repository.mark_failed(message.id, str(error))
                failed += 1
                await self.session.commit()
                continue
            if pacer is not None:
                await pacer.wait_turn(message.chat_id)
            try:
                message_ref = await self._deliver(client, codes[message.bot_id], message)
            except MessengerPermanentError as error:
                await self.repository.mark_failed(message.id, str(error))
                if any(marker in str(error).lower() for marker in DEAD_CHAT_MARKERS):
                    await self.repository.unsubscribe_chat(message.bot_id, message.chat_id)
                failed += 1
            except MessengerRateLimitError as error:
                await self.repository.mark_rate_limited(
                    message.id, str(error), retry_after_seconds=error.retry_after or backoff_seconds
                )
                retried += 1
            except MessengerTemporaryError as error:
                if await self.repository.mark_retry(
                    message.id, str(error), max_attempts=max_attempts, backoff_seconds=backoff_seconds
                ):
                    retried += 1
                else:
                    failed += 1
            else:
                await self.repository.mark_sent(message.id, message_ref)
                sent += 1
            await self.session.commit()
        return DeliveryReport(sent, retried, failed)

    @staticmethod
    async def _deliver(client: RelayClient, bot_code: str, message: OutgoingMessageModel) -> str | None:
        """One queued row, by its kind. A plain message calls `send` exactly as before v1.1."""
        if message.kind == EDIT_KIND:
            await client.edit_message(
                bot_code=bot_code,
                recipient=str(message.chat_id),
                message_ref=str(message.target_ref or ""),
                text=message.text,
                buttons=message.buttons,
                idempotency_key=str(message.id),
            )
            return message.target_ref
        if message.kind == CALLBACK_ANSWER_KIND:
            callback_id = str(message.target_ref or "")
            await client.answer_callback(bot_code=bot_code, callback_id=callback_id, text=message.text)
            return None
        extra: dict[str, object] = {}
        if message.buttons:
            extra["buttons"] = message.buttons
        if message.reply_to:
            extra["reply_to"] = message.reply_to
        return await client.send(
            bot_code=bot_code,
            recipient=str(message.chat_id),
            text=message.text,
            idempotency_key=str(message.id),
            attachment=message.attachment,
            **extra,  # type: ignore[arg-type]
        )
