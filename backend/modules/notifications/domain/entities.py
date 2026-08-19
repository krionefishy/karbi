import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CHAT_AUDIENCE = "chat"
SELLER_AUDIENCE = "seller_subscribers"


@dataclass(frozen=True, slots=True)
class Bot:
    id: uuid.UUID
    code: str
    username: str
    title: str


@dataclass(frozen=True, slots=True)
class Invite:
    token: str
    url: str
    expires_at: datetime
    seller_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Audience:
    """Who a message is for, in the producer's terms.

    Automations know sellers, not chats: resolving one into the other is the
    notifications module's job, so a second chat on the same seller needs no
    change on the producing side.
    """

    kind: str
    seller_id: uuid.UUID | None = None
    chat_id: int | None = None

    @classmethod
    def parse(cls, payload: Any) -> "Audience":
        if not isinstance(payload, dict):
            raise ValueError("audience must be an object")
        kind = payload.get("type")
        if kind == SELLER_AUDIENCE:
            return cls(SELLER_AUDIENCE, seller_id=uuid.UUID(str(payload["seller_id"])))
        if kind == CHAT_AUDIENCE:
            return cls(CHAT_AUDIENCE, chat_id=int(payload["chat_id"]))
        raise ValueError(f"unknown audience type: {kind!r}")


@dataclass(frozen=True, slots=True)
class MessageRequest:
    """One notification event as it arrives from Kafka."""

    message_id: str
    bot_code: str
    audience: Audience
    template: str
    dedupe_key: str
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "MessageRequest":
        message_id = str(payload.get("message_id") or payload.get("event_id") or "")
        bot_code = str(payload.get("bot") or "")
        template = str(payload.get("template") or "")
        if not message_id or not bot_code or not template:
            raise ValueError("message_id, bot and template are required")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        return cls(
            message_id=message_id,
            bot_code=bot_code,
            audience=Audience.parse(payload.get("audience")),
            template=template,
            dedupe_key=str(payload.get("dedupe_key") or message_id),
            params=params,
        )
