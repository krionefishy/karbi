import logging
from dataclasses import dataclass
from typing import Any

import httpx


class TelegramPermanentError(Exception):
    """Telegram will answer the same way next time: retrying is pointless."""


class TelegramTemporaryError(Exception):
    """Network trouble or a 5xx — the same call may well succeed later."""


class TelegramConflictError(TelegramTemporaryError):
    """Another poller holds getUpdates for this token."""


class TelegramRateLimitError(TelegramTemporaryError):
    """Telegram asks to slow down; retry_after says for how long."""

    def __init__(self, description: str, retry_after: float | None = None) -> None:
        super().__init__(description)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Update:
    update_id: int
    chat_id: int
    text: str
    user_id: int | None
    username: str
    first_name: str


class TelegramClient:
    """The slice of the Bot API we use: identify, poll, send.

    Every call carries the token explicitly — one process serves many bots and
    must never mix them up.
    """

    def __init__(
        self,
        base_url: str = "https://api.telegram.org",
        request_timeout_seconds: float = 40.0,
        poll_timeout_seconds: int = 25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(request_timeout_seconds, connect=10.0)
        self.poll_timeout_seconds = poll_timeout_seconds
        self.logger = logging.getLogger("notifications.telegram")

    async def me(self, token: str) -> dict[str, Any]:
        return await self._call(token, "getMe", {})

    async def updates(self, token: str, offset: int) -> list[Update]:
        payload = await self._call(
            token,
            "getUpdates",
            {
                "offset": offset,
                "timeout": self.poll_timeout_seconds,
                "allowed_updates": ["message"],
            },
        )
        return [update for raw in payload if (update := self._parse(raw)) is not None]

    async def send(self, token: str, chat_id: int, text: str) -> int | None:
        payload = await self._call(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )
        message_id = payload.get("message_id") if isinstance(payload, dict) else None
        return int(message_id) if isinstance(message_id, int) else None

    @staticmethod
    def _parse(raw: Any) -> Update | None:
        if not isinstance(raw, dict):
            return None
        message = raw.get("message")
        update_id = raw.get("update_id")
        if not isinstance(message, dict) or not isinstance(update_id, int):
            return None
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return None
        sender = message.get("from") or {}
        return Update(
            update_id=update_id,
            chat_id=chat_id,
            text=str(message.get("text") or ""),
            user_id=sender.get("id") if isinstance(sender.get("id"), int) else None,
            username=str(sender.get("username") or ""),
            first_name=str(sender.get("first_name") or ""),
        )

    async def _call(self, token: str, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self.base_url}/bot{token}/{method}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload)
        except httpx.HTTPError as error:
            raise TelegramTemporaryError(f"{method} failed: {type(error).__name__}") from error
        return self._unwrap(method, response)

    @staticmethod
    def _unwrap(method: str, response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError as error:
            raise TelegramTemporaryError(f"{method} returned a non-JSON body") from error
        if body.get("ok"):
            return body.get("result")
        description = str(body.get("description") or "Telegram rejected the request")
        # 409 means a second poller took the token; 429 and 5xx pass with time.
        if response.status_code == 409:
            raise TelegramConflictError(description)
        if response.status_code == 429:
            retry_after = (body.get("parameters") or {}).get("retry_after")
            raise TelegramRateLimitError(
                description, float(retry_after) if isinstance(retry_after, int | float) else None
            )
        if response.status_code >= 500:
            raise TelegramTemporaryError(description)
        raise TelegramPermanentError(description)
