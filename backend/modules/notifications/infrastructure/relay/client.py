"""Client for the messenger relay.

The relay is the only host that can reach the messenger, so this is the only
way out of the country for a notification. Nothing messenger-specific crosses
this boundary: a bot is a code, a chat is an opaque `recipient` string, and a
message is plain text.

Failures are reported in the delivery vocabulary the dispatcher already speaks,
so switching from direct Telegram calls to the relay changed no retry logic.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import jwt

from backend.modules.notifications.infrastructure.telegram import (
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramTemporaryError,
)
from backend.shared.settings import RelayConfig

BOTS_PATH = "/api/v1/bots"
SEND_PATH = "/api/v1/messages/send"
UPDATES_PATH = "/api/v1/updates"


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
        self.logger = logging.getLogger("notifications.relay")

    async def send(self, *, bot_code: str, recipient: str, text: str, idempotency_key: str) -> str | None:
        """Hand one message to the relay. Returns the messenger's reference, if any."""
        response = await self._post(
            SEND_PATH,
            {
                "bot_code": bot_code,
                "recipient": recipient,
                "text": text,
                "idempotency_key": idempotency_key,
            },
        )
        body = self._body(response)
        if response.status_code == 200:
            reference = body.get("message_ref")
            return str(reference) if reference is not None else None
        raise self._delivery_error(response, body)

    async def updates(self, *, bot_code: str, after: int, wait: int, limit: int = 50) -> list[RelayUpdate]:
        """Ask the relay for everything past our cursor.

        We fetch rather than being pushed to: from abroad this server cannot be
        reached at all, so the relay holds updates and we come for them. There is
        no acknowledgement — `after` is the whole protocol, and the cursor behind
        it lives in our own database.
        """
        response = await self._get(
            UPDATES_PATH,
            {"bot_code": bot_code, "after": after, "limit": limit, "wait": wait},
            # The relay holds the request for `wait` seconds; the read timeout
            # has to outlast that or every quiet poll dies of its own timeout.
            timeouts=httpx.Timeout(wait + self._config.request_timeout_seconds, connect=10.0),
        )
        body = self._body(response)
        if response.status_code != 200:
            raise self._delivery_error(response, body)
        raw = body.get("updates")
        if not isinstance(raw, list):
            raise TelegramTemporaryError("the relay returned no update list")
        return [self._parse(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _parse(item: dict[str, object]) -> RelayUpdate:
        raw_sender = item.get("sender")
        sender: dict[str, object] = raw_sender if isinstance(raw_sender, dict) else {}
        external_id = sender.get("external_id")
        event_id = item.get("event_id")
        return RelayUpdate(
            event_id=event_id if isinstance(event_id, int) else 0,
            recipient=str(item.get("recipient") or ""),
            text=str(item.get("text") or ""),
            external_id=str(external_id) if external_id is not None else None,
            username=str(sender.get("username") or ""),
            display_name=str(sender.get("display_name") or ""),
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
            raise TelegramTemporaryError(f"relay unreachable: {type(error).__name__}") from error

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        url = f"{self._config.base_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=self._verify) as client:
                return await client.post(url, json=payload, headers={"Authorization": self._authorization()})
        except httpx.HTTPError as error:
            # The relay being unreachable says nothing about the message itself.
            raise TelegramTemporaryError(f"relay unreachable: {type(error).__name__}") from error

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
            return TelegramRateLimitError(detail, retry_after=int(retry_after) if retry_after else None)
        if response.status_code == 422:
            # The messenger refused for a reason another attempt cannot fix:
            # unknown chat, blocked bot. The relay passes its wording through,
            # which is what the dead-chat markers are matched against.
            return TelegramPermanentError(detail)
        # Everything else — 401 from a clock skew, 404 for a bot nobody
        # registered on the relay yet, 502 from the messenger — is an operator
        # problem that retrying survives once it is fixed.
        return TelegramTemporaryError(f"relay answered {response.status_code}: {detail}")
