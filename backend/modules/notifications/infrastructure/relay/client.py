import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from backend.modules.notifications.domain import (
    UPDATE_CALLBACK,
    UPDATE_MESSAGE,
    Attachment,
    Callback,
    MessengerFileTooLargeError,
    MessengerPermanentError,
    MessengerRateLimitError,
    MessengerTemporaryError,
)
from backend.shared.settings import RelayConfig

BOTS_PATH = "/api/v1/bots"
SEND_PATH = "/api/v1/messages/send"
EDIT_PATH = "/api/v1/messages/edit"
CALLBACK_ANSWER_PATH = "/api/v1/callbacks/answer"
FILES_PATH = "/api/v1/files"
UPDATES_PATH = "/api/v1/updates"
CAPABILITIES_PATH = "/api/v1/capabilities"
# Что нужно от релея интерактивным ботам (контракт v1.1, `GET /api/v1/capabilities`).
INTERACTIVE_FEATURES = frozenset({"attachments_in", "files", "buttons", "reply_to", "callbacks", "edit"})
# Контракт v1.1: file_ref непрозрачен, но в путь URL попадает только такой.
_FILE_REF = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
INTERACTIVE_DISABLED = "interactive relay features are disabled (relay.interactive_enabled)"


@dataclass(frozen=True, slots=True)
class RelayUpdate:
    """One inbound message, already in our terms.

    `recipient` and `external_id` are addresses the messenger handed the relay;
    this side stores and echoes them without interpreting what they mean.
    """

    event_id: int
    recipient: str
    text: str
    external_id: str | None
    username: str
    display_name: str
    # Контракт v1.1; у старого релея этих полей нет, и апдейт остаётся текстовым сообщением.
    kind: str = UPDATE_MESSAGE
    chat_type: str = ""
    message_ref: str = ""
    reply_to_ref: str = ""
    media_group_id: str = ""
    caption: str = ""
    forwarded: bool = False
    attachments: tuple[Attachment, ...] = ()
    callback: Callback | None = None


@dataclass(frozen=True, slots=True)
class RelayBot:
    """What the relay knows about a bot once it has introduced itself."""

    title: str
    invite_link_template: str


class RelayClient:
    def __init__(self, config: RelayConfig) -> None:
        self._config = config
        self._verify: str | bool = config.verify
        if config.verify.lower() in {"true", "1", "yes"}:
            self._verify = True
        elif config.verify.lower() in {"false", "0", "no"}:
            self._verify = False
        self._timeout = httpx.Timeout(config.request_timeout_seconds, connect=10.0)
        # Флаг из настроек; воркер уведомлений гасит его, если релей не подтвердил контракт v1.1.
        self.interactive_enabled = config.interactive_enabled
        self.logger = logging.getLogger("notifications.relay")

    async def send(
        self,
        *,
        bot_code: str,
        recipient: str,
        text: str,
        idempotency_key: str,
        attachment: dict | None = None,
        buttons: list | None = None,
        reply_to: str | None = None,
    ) -> str | None:
        """Hand one message to the relay. Returns the messenger's reference, if any.

        `attachment` travels only when the relay is known to understand it
        (`relay.photos_enabled`); an older relay would refuse the whole message.
        `buttons` and `reply_to` are contract v1.1 and travel only with
        `relay.interactive_enabled` — without it the message goes out plain.
        """
        payload: dict[str, object] = {
            "bot_code": bot_code,
            "recipient": recipient,
            "text": text,
            "idempotency_key": idempotency_key,
        }
        if attachment and self._config.photos_enabled:
            payload["attachment"] = attachment
        if self.interactive_enabled:
            if buttons:
                payload["buttons"] = buttons
            if reply_to and reply_to.isdigit():
                payload["reply_to"] = reply_to
        response = await self._post(SEND_PATH, payload)
        body = self._body(response)
        if response.status_code == 200:
            reference = body.get("message_ref")
            return str(reference) if reference is not None else None
        raise self._delivery_error(response, body)

    async def updates(self, *, bot_code: str, after: int, wait: int, limit: int = 50) -> list[RelayUpdate]:
        """Ask the relay for everything past our cursor.

        We fetch rather than being pushed to: this server always opens the
        connection, so the relay holds updates and we come for them. There is
        no acknowledgement — `after` is the whole protocol, and the cursor behind
        it lives in our own database.
        """
        response = await self._get(
            UPDATES_PATH,
            {"bot_code": bot_code, "after": after, "limit": limit, "wait": wait},
            timeouts=httpx.Timeout(wait + self._config.request_timeout_seconds, connect=10.0),
        )
        body = self._body(response)
        if response.status_code != 200:
            raise self._delivery_error(response, body)
        raw = body.get("updates")
        if not isinstance(raw, list):
            raise MessengerTemporaryError("the relay returned no update list")
        return [self._parse(item) for item in raw if isinstance(item, dict)]

    async def edit_message(
        self,
        *,
        bot_code: str,
        recipient: str,
        message_ref: str,
        text: str,
        buttons: list | None,
        idempotency_key: str,
    ) -> None:
        """Rewrite a message the bot sent; `buttons=None` takes the keyboard away."""
        self._require_interactive()
        response = await self._post(
            EDIT_PATH,
            {
                "bot_code": bot_code,
                "recipient": recipient,
                "message_ref": message_ref,
                "text": text,
                "buttons": buttons,
                "idempotency_key": idempotency_key,
            },
        )
        if response.status_code != 200:
            raise self._delivery_error(response, self._body(response))

    async def answer_callback(self, *, bot_code: str, callback_id: str, text: str = "", alert: bool = False) -> None:
        """Stop the spinner on a pressed button; a too old press answers 422 and is not retried."""
        self._require_interactive()
        payload: dict[str, object] = {"bot_code": bot_code, "callback_id": callback_id, "alert": alert}
        if text:
            payload["text"] = text[:200]
        response = await self._post(CALLBACK_ANSWER_PATH, payload)
        if response.status_code != 200:
            raise self._delivery_error(response, self._body(response))

    async def fetch_file(self, *, bot_code: str, file_ref: str, max_bytes: int) -> bytes:
        """Download one inbound file, streamed and cut off past `max_bytes`.

        The relay checks the size too; this side does not trust it and stops
        reading the moment the body grows past the limit, so a lying relay or a
        lying header costs at most `max_bytes` of memory. The whole call has one
        deadline (`relay.file_timeout_seconds`), not a per-read one.
        """
        self._require_interactive()
        if not _FILE_REF.fullmatch(file_ref):
            raise MessengerPermanentError("file reference is not in the relay format")
        url = f"{self._config.base_url.rstrip('/')}{FILES_PATH}/{file_ref}"
        params: dict[str, str | int] = {"bot_code": bot_code, "max_bytes": max_bytes}
        try:
            async with asyncio.timeout(self._config.file_timeout_seconds):
                async with (
                    httpx.AsyncClient(timeout=self._timeout, verify=self._verify) as client,
                    client.stream(
                        "GET", url, params=params, headers={"Authorization": self._authorization()}
                    ) as response,
                ):
                    if response.status_code != 200:
                        await response.aread()
                        raise self._file_error(response)
                    declared = response.headers.get("Content-Length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        raise MessengerFileTooLargeError(f"file is {declared} bytes, the limit is {max_bytes}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise MessengerFileTooLargeError(f"file is over the {max_bytes} bytes limit")
                        chunks.append(chunk)
                    # Релей обрывает поток, переросший лимит: недокачанное тело — ошибка, а не файл.
                    if declared and declared.isdigit() and total != int(declared):
                        raise MessengerTemporaryError("relay file stream ended early")
                    return b"".join(chunks)
        except TimeoutError as error:
            raise MessengerTemporaryError("relay file download timed out") from error
        except httpx.HTTPError as error:
            raise MessengerTemporaryError(f"relay unreachable: {type(error).__name__}") from error

    def _require_interactive(self) -> None:
        if not self.interactive_enabled:
            raise MessengerPermanentError(INTERACTIVE_DISABLED)

    async def supports_interactive(self) -> bool:
        """Знает ли релей контракт v1.1. Релей v1.0 отвечает на capabilities 404 — это «нет».

        Сетевая ошибка или 5xx — `MessengerTemporaryError`: ответа ещё нет, спросить позже.
        """
        response = await self._get(CAPABILITIES_PATH, {})
        if response.status_code == 404:
            return False
        body = self._body(response)
        if response.status_code != 200:
            raise self._delivery_error(response, body)
        features = body.get("features")
        contract = str(body.get("contract") or "")
        known = {str(item) for item in features} if isinstance(features, list) else set()
        return _version(contract) >= (1, 1) and known >= INTERACTIVE_FEATURES

    def _file_error(self, response: httpx.Response) -> Exception:
        body = self._body(response)
        detail = str(body.get("detail") or response.status_code)
        if response.status_code == 413:
            return MessengerFileTooLargeError(detail)
        if response.status_code in (404, 410):
            return MessengerPermanentError(f"file is gone: {detail}")
        return self._delivery_error(response, body)

    @staticmethod
    def _parse(item: dict[str, object]) -> RelayUpdate:
        """Tolerant on purpose: an older relay sends v1, a newer one may add fields we ignore."""
        raw_sender = item.get("sender")
        sender: dict[str, object] = raw_sender if isinstance(raw_sender, dict) else {}
        external_id = sender.get("external_id")
        event_id = item.get("event_id")
        kind = str(item.get("kind") or UPDATE_MESSAGE)
        callback = Callback.parse(item.get("callback"))
        if kind not in (UPDATE_MESSAGE, UPDATE_CALLBACK) or (kind == UPDATE_CALLBACK and callback is None):
            kind, callback = UPDATE_MESSAGE, None
        raw_attachments = item.get("attachments")
        listed = raw_attachments if isinstance(raw_attachments, list) else []
        attachments = tuple(parsed for parsed in (Attachment.parse(raw) for raw in listed) if parsed is not None)
        return RelayUpdate(
            event_id=event_id if isinstance(event_id, int) else 0,
            recipient=str(item.get("recipient") or ""),
            text=str(item.get("text") or ""),
            external_id=str(external_id) if external_id is not None else None,
            username=str(sender.get("username") or ""),
            display_name=str(sender.get("display_name") or ""),
            kind=kind,
            chat_type=str(item.get("chat_type") or ""),
            message_ref=_ref(item.get("message_ref")),
            reply_to_ref=_ref(item.get("reply_to_ref")),
            media_group_id=_ref(item.get("media_group_id")),
            caption=str(item.get("caption") or "")[:1024],
            forwarded=item.get("forwarded") is True,
            attachments=attachments,
            callback=callback,
        )

    async def register_bot(self, *, bot_code: str, token: str) -> RelayBot:
        """Hand the token to the relay, which stores it and never gives it back."""
        response = await self._post(BOTS_PATH, {"bot_code": bot_code, "action": "upsert", "token": token})
        body = self._body(response)
        if response.status_code == 200:
            return RelayBot(
                title=str(body.get("title") or ""),
                invite_link_template=str(body.get("invite_link_template") or ""),
            )
        raise self._delivery_error(response, body)

    async def delete_bot(self, bot_code: str) -> None:
        response = await self._post(BOTS_PATH, {"bot_code": bot_code, "action": "delete"})
        if response.status_code != 200:
            raise self._delivery_error(response, self._body(response))

    def _authorization(self) -> str:
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": "main",
                "jti": str(uuid.uuid4()),
                "iat": now,
                "exp": now + timedelta(seconds=self._config.jwt_ttl_seconds),
                "iss": self._config.issuer,
                "aud": self._config.audience,
            },
            self._config.jwt_secret,
            algorithm="HS256",
        )
        return f"Bearer {token}"

    async def _get(
        self, path: str, params: dict[str, str | int], timeouts: httpx.Timeout | None = None
    ) -> httpx.Response:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeouts or self._timeout, verify=self._verify) as client:
                return await client.get(url, params=params, headers={"Authorization": self._authorization()})
        except httpx.HTTPError as error:
            raise MessengerTemporaryError(f"relay unreachable: {type(error).__name__}") from error

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify) as client:
                return await client.post(url, json=payload, headers={"Authorization": self._authorization()})
        except httpx.HTTPError as error:
            raise MessengerTemporaryError(f"relay unreachable: {type(error).__name__}") from error

    @staticmethod
    def _body(response: httpx.Response) -> dict[str, object]:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @staticmethod
    def _delivery_error(response: httpx.Response, body: dict[str, object]) -> Exception:
        detail = str(body.get("detail") or response.text or response.status_code)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            return MessengerRateLimitError(detail, retry_after=int(retry_after) if retry_after else None)
        if response.status_code == 422:
            return MessengerPermanentError(detail)
        return MessengerTemporaryError(f"relay answered {response.status_code}: {detail}")


def _ref(value: object) -> str:
    return str(value)[:64] if value is not None and value != "" else ""


def _version(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else (0,)
