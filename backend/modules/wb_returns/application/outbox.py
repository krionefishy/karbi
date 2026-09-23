"""Ответ в конкретный чат — событие сообщения с аудиторией «чат»."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository


def chat_aggregate(chat_id: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-chat:{chat_id}")


def publish_chat_message(
    session: AsyncSession,
    *,
    bot_code: str,
    chat_id: int,
    template: str,
    params: dict[str, Any],
    dedupe_key: str,
    event_type: str = "ReturnsChatMessageRequested",
) -> None:
    OutboxRepository(session).add(
        aggregate_id=chat_aggregate(chat_id),
        event_type=event_type,
        topic=NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
        payload={
            "message_id": str(uuid.uuid4()),
            "bot": bot_code,
            "audience": {"type": "chat", "chat_id": chat_id},
            "template": template,
            "dedupe_key": dedupe_key,
            "params": params,
        },
    )
