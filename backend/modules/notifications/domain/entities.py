import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CHAT_AUDIENCE = "chat"
SELLER_AUDIENCE = "seller_subscribers"

UPDATE_MESSAGE = "message"
UPDATE_CALLBACK = "callback"
UPDATE_KINDS = (UPDATE_MESSAGE, UPDATE_CALLBACK)

# Что лежит в строке очереди: новое сообщение, правка отправленного или ответ на нажатие кнопки.
MESSAGE_KIND = "message"
EDIT_KIND = "edit"
CALLBACK_ANSWER_KIND = "callback_answer"
OUTGOING_KINDS = (MESSAGE_KIND, EDIT_KIND, CALLBACK_ANSWER_KIND)

# Ограничения контракта релея v1.1 на кнопки: ряды × кнопки, подпись и действие (байты UTF-8).
MAX_BUTTON_ROWS = 8
MAX_BUTTONS_PER_ROW = 8
MAX_BUTTON_LABEL = 64
MAX_BUTTON_ACTION_BYTES = 64


@dataclass(frozen=True, slots=True)
class Attachment:
    """Файл во входящем сообщении. `file_ref` — непрозрачная ссылка релея, скачать по ней можно только через него."""

    kind: str
    file_ref: str
    size: int | None = None
    width: int | None = None
    height: int | None = None
    mime: str = ""
    file_name: str = ""

    @classmethod
    def parse(cls, payload: Any) -> "Attachment | None":
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("kind") or "")
        file_ref = str(payload.get("file_ref") or "")
        if not kind or not file_ref:
            return None
        return cls(
            kind=kind,
            file_ref=file_ref,
            size=_int_or_none(payload.get("size")),
            width=_int_or_none(payload.get("width")),
            height=_int_or_none(payload.get("height")),
            mime=str(payload.get("mime") or ""),
            file_name=str(payload.get("file_name") or ""),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "file_ref": self.file_ref,
            "size": self.size,
            "width": self.width,
            "height": self.height,
            "mime": self.mime,
            "file_name": self.file_name,
        }


@dataclass(frozen=True, slots=True)
class Callback:
    """Нажатие inline-кнопки: `id` нужен для ответа, `message_ref` — сообщение, на котором кнопка."""

    id: str
    data: str
    message_ref: str = ""

    @classmethod
    def parse(cls, payload: Any) -> "Callback | None":
        if not isinstance(payload, dict) or not payload.get("id"):
            return None
        return cls(
            id=str(payload["id"]),
            data=str(payload.get("data") or ""),
            message_ref=str(payload.get("message_ref") or ""),
        )

    def as_payload(self) -> dict[str, Any]:
        return {"id": self.id, "data": self.data, "message_ref": self.message_ref}


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def parse_buttons(raw: Any) -> tuple[tuple[tuple[str, str], ...], ...] | None:
    """Кнопки `[[{"label", "action"}]]` → ряды пар; пусто — `None`. Нарушение лимитов контракта — ValueError."""
    if raw is None or raw == []:
        return None
    if not isinstance(raw, list) or len(raw) > MAX_BUTTON_ROWS:
        raise ValueError("buttons must be a list of at most 8 rows")
    rows = []
    for row in raw:
        if not isinstance(row, list) or not row or len(row) > MAX_BUTTONS_PER_ROW:
            raise ValueError("each button row must hold 1-8 buttons")
        items = []
        for button in row:
            if not isinstance(button, dict):
                raise ValueError("a button must be an object")
            label = str(button.get("label") or "")
            action = str(button.get("action") or "")
            if not 1 <= len(label) <= MAX_BUTTON_LABEL:
                raise ValueError("button label must be 1-64 characters")
            if not 1 <= len(action.encode()) <= MAX_BUTTON_ACTION_BYTES:
                raise ValueError("button action must be 1-64 bytes")
            items.append((label, action))
        rows.append(tuple(items))
    return tuple(rows)


def buttons_payload(rows: tuple[tuple[tuple[str, str], ...], ...] | None) -> list[list[dict[str, str]]] | None:
    if not rows:
        return None
    return [[{"label": label, "action": action} for label, action in row] for row in rows]


@dataclass(frozen=True, slots=True)
class Bot:
    id: uuid.UUID
    code: str
    title: str
    invite_link_template: str
    # Интерактивный бот (кнопки, файлы, свои /start): апдейты целиком уходят его автоматизации,
    # подписки `invite_links` у него не работают.
    interactive: bool = False


@dataclass(frozen=True, slots=True)
class BotDelivery:
    """Доходят ли сообщения бота: последнее доставленное и что не ушло после него."""

    last_sent_at: datetime | None = None
    failed_count: int = 0
    failed_since: datetime | None = None
    last_error: str | None = None


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
    kind: str = UPDATE_MESSAGE
    chat_type: str = ""
    message_ref: str = ""
    reply_to_ref: str = ""
    media_group_id: str = ""
    caption: str = ""
    forwarded: bool = False
    attachments: tuple["Attachment", ...] = ()
    callback: "Callback | None" = None


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
    # `edit` правит сообщение `target_ref`, `callback_answer` отвечает на нажатие `target_ref`;
    # оба — только в один чат. Кнопки рисует шаблон, ответ-цитату задаёт `params.reply_to`.
    kind: str = MESSAGE_KIND
    target_ref: str | None = None

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
        kind = str(payload.get("kind") or MESSAGE_KIND)
        if kind not in OUTGOING_KINDS:
            raise ValueError(f"unknown message kind: {kind!r}")
        audience = Audience.parse(payload.get("audience"))
        target_ref = str(payload.get("target_ref") or "")[:64] or None
        if kind != MESSAGE_KIND and (target_ref is None or audience.kind != CHAT_AUDIENCE):
            raise ValueError(f"{kind} needs target_ref and a chat audience")
        return cls(
            message_id=message_id,
            bot_code=bot_code,
            audience=audience,
            template=template,
            dedupe_key=str(payload.get("dedupe_key") or message_id),
            params=params,
            kind=kind,
            target_ref=target_ref,
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


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    """Апдейт интерактивного бота целиком, как он едет в `notifications.telegram.update.received`.

    Модуль уведомлений не толкует ни текст, ни кнопки: что значит `/start <токен>` или нажатие,
    решает автоматизация бота. `update_id` — номер апдейта у мессенджера, по нему автоматизация
    отсекает повтор.
    """

    bot_code: str
    update_id: int
    kind: str
    chat_id: int
    chat_type: str
    user_id: int | None
    username: str
    first_name: str
    message_ref: str
    reply_to_ref: str
    text: str
    caption: str
    media_group_id: str
    forwarded: bool
    attachments: tuple[Attachment, ...]
    callback: Callback | None
    event_id: str = ""

    @classmethod
    def from_update(cls, bot_code: str, update: Update) -> "UpdateEvent":
        return cls(
            bot_code=bot_code,
            update_id=update.update_id,
            kind=update.kind,
            chat_id=update.chat_id,
            chat_type=update.chat_type,
            user_id=update.user_id,
            username=update.username,
            first_name=update.first_name,
            message_ref=update.message_ref,
            reply_to_ref=update.reply_to_ref,
            text=update.text,
            caption=update.caption,
            media_group_id=update.media_group_id,
            forwarded=update.forwarded,
            attachments=update.attachments,
            callback=update.callback,
        )

    def payload(self) -> dict[str, Any]:
        return {
            "bot": self.bot_code,
            "update_id": self.update_id,
            "kind": self.kind,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "message_ref": self.message_ref,
            "reply_to_ref": self.reply_to_ref,
            "text": self.text,
            "caption": self.caption,
            "media_group_id": self.media_group_id,
            "forwarded": self.forwarded,
            "attachments": [item.as_payload() for item in self.attachments],
            "callback": self.callback.as_payload() if self.callback else None,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "UpdateEvent":
        bot_code = str(payload.get("bot") or "")
        update_id = payload.get("update_id")
        if not bot_code or not isinstance(update_id, int) or isinstance(update_id, bool) or update_id <= 0:
            raise ValueError("bot and a positive update_id are required")
        kind = str(payload.get("kind") or UPDATE_MESSAGE)
        if kind not in UPDATE_KINDS:
            raise ValueError(f"unknown update kind: {kind!r}")
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, int) or isinstance(chat_id, bool):
            raise ValueError("chat_id must be an integer")
        raw_attachments = payload.get("attachments") or []
        if not isinstance(raw_attachments, list):
            raise ValueError("attachments must be a list")
        attachments = tuple(item for item in (Attachment.parse(raw) for raw in raw_attachments) if item)
        callback = Callback.parse(payload.get("callback"))
        if kind == UPDATE_CALLBACK and callback is None:
            raise ValueError("a callback update needs a callback")
        return cls(
            bot_code=bot_code,
            update_id=update_id,
            kind=kind,
            chat_id=chat_id,
            chat_type=str(payload.get("chat_type") or ""),
            user_id=_int_or_none(payload.get("user_id")),
            username=str(payload.get("username") or ""),
            first_name=str(payload.get("first_name") or ""),
            message_ref=str(payload.get("message_ref") or ""),
            reply_to_ref=str(payload.get("reply_to_ref") or ""),
            text=str(payload.get("text") or ""),
            caption=str(payload.get("caption") or ""),
            media_group_id=str(payload.get("media_group_id") or ""),
            forwarded=bool(payload.get("forwarded")),
            attachments=attachments,
            callback=callback,
            event_id=str(payload.get("event_id") or ""),
        )
