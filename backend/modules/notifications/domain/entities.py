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
    title: str
    invite_link_template: str


@dataclass(frozen=True, slots=True)
class Update:
    """One inbound message, in our own terms.

    `chat_id` and `user_id` are addresses the messenger handed us; this side
    stores and echoes them without interpreting what they mean.
    """

    update_id: int
    chat_id: int
    text: str
    user_id: int | None
    username: str
    first_name: str


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


COMMAND_START = "start"


@dataclass(frozen=True, slots=True)
class CommandEvent:
    """Команда из чата, отданная автоматизации бота: `/help`, `/qr` или удачный `/start`.

    Модуль уведомлений знает только подписки; что ответить на `/returns`, знает
    автоматизация. Событие несёт чат и его селлеров, чтобы ответ не ходил в базу
    уведомлений за тем, что уже известно.
    """

    event_id: str
    bot_code: str
    chat_id: int
    command: str
    argument: str
    text: str
    sellers: tuple[tuple[uuid.UUID, str], ...]
    username: str = ""
    first_name: str = ""

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "CommandEvent":
        event_id = str(payload.get("event_id") or "")
        bot_code = str(payload.get("bot") or "")
        command = str(payload.get("command") or "")
        if not event_id or not bot_code or not command:
            raise ValueError("event_id, bot and command are required")
        raw_sellers = payload.get("sellers") or []
        if not isinstance(raw_sellers, list):
            raise ValueError("sellers must be a list")
        sellers = tuple(
            (uuid.UUID(str(item["seller_id"])), str(item.get("seller_name") or ""))
            for item in raw_sellers
            if isinstance(item, dict) and item.get("seller_id")
        )
        return cls(
            event_id=event_id,
            bot_code=bot_code,
            chat_id=int(payload["chat_id"]),
            command=command,
            argument=str(payload.get("argument") or ""),
            text=str(payload.get("text") or ""),
            sellers=sellers,
            username=str(payload.get("username") or ""),
            first_name=str(payload.get("first_name") or ""),
        )
