from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_core.domain.mirror import ChatEvent, is_review_prompt
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

CHAT_BUCKET = "chat"
EVENTS_PATH = "/api/v1/seller/events"


@dataclass(frozen=True, slots=True)
class ChatEventsPage:
    """Страница ленты: события по возрастанию времени и курсор следующей.

    `next` — время последнего события в миллисекундах; на пустой странице WB
    возвращает тот же курсор, что получил.
    """

    events: list[ChatEvent]
    next: int | None


class WBChatClient(WBJsonClient):
    """Лента событий чатов с покупателями: одна на кабинет, читается курсором от старых к новым."""

    bucket = CHAT_BUCKET
    api_name = "WB Buyers Chat API"
    category = "Чат с покупателями"

    async def events(self, seller_id: str, *, after: int | None) -> ChatEventsPage:
        """События после курсора `after`; без курсора — с самого начала ленты."""
        params = {"next": after} if after is not None else None
        payload = await self.request("GET", EVENTS_PATH, seller_id, params=params)
        if not isinstance(payload, dict):
            raise WBPermanentError(f"{self.api_name}: лента событий пришла не объектом")
        if payload.get("errors"):
            raise WBPermanentError(f"{self.api_name}: {payload['errors']}")
        result = payload.get("result") or {}
        rows = result.get("events") or [] if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: лента событий пришла в неожиданном виде")
        cursor = result.get("next")
        events = [event for row in rows if (event := self._event(row)) is not None]
        return ChatEventsPage(events=events, next=int(cursor) if isinstance(cursor, int) and cursor > 0 else None)

    @staticmethod
    def _event(row: Any) -> ChatEvent | None:
        """Сообщение ленты. Строки без идентификатора, чата или времени пропускаются:
        их не к чему привязать, а других типов событий, кроме сообщений, WB не шлёт."""
        if not isinstance(row, dict) or row.get("eventType") != "message":
            return None
        event_id, chat_id, stamp = row.get("eventID"), row.get("chatID"), row.get("addTimestamp")
        if not event_id or not chat_id or not isinstance(stamp, int):
            return None
        message = row.get("message") or {}
        attachments = message.get("attachments") or {}
        card = attachments.get("goodCard") or {}
        text = message.get("text") or None
        sender, source = str(row.get("sender") or ""), str(row.get("source") or "")
        nm_id = card.get("nmID")
        return ChatEvent(
            event_id=str(event_id),
            chat_id=str(chat_id),
            sender=sender,
            source=source,
            added_at=datetime.fromtimestamp(stamp / 1000, tz=UTC),
            is_new_chat=bool(row.get("isNewChat")),
            review_prompt=is_review_prompt(sender, source, text),
            nm_id=nm_id if isinstance(nm_id, int) and nm_id > 0 else None,
            rid=str(card.get("rid")) if card.get("rid") else None,
            text=text,
            has_attachments=bool(attachments.get("images") or attachments.get("files")),
        )
