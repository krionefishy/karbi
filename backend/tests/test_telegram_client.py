import json

import httpx
import pytest
import respx

from backend.modules.notifications.infrastructure.telegram import (
    TelegramClient,
    TelegramConflictError,
    TelegramPermanentError,
    TelegramRateLimitError,
    TelegramTemporaryError,
)

BASE = "http://telegram.test"
TOKEN = "1234:secret"


def client() -> TelegramClient:
    return TelegramClient(base_url=BASE, request_timeout_seconds=5.0, poll_timeout_seconds=1)


def message(update_id: int, text: str, chat_id: int = 555) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": 777, "username": "buyer", "first_name": "Иван"},
            "text": text,
        },
    }


@respx.mock
async def test_updates_are_parsed_and_non_messages_ignored() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    message(10, "/start abc"),
                    {"update_id": 11, "edited_message": {"chat": {"id": 1}}},
                    {"update_id": 12, "message": {"chat": {}, "text": "no chat id"}},
                ],
            },
        )
    )

    updates = await client().updates(TOKEN, offset=10)

    assert [(item.update_id, item.text, item.chat_id) for item in updates] == [(10, "/start abc", 555)]
    assert updates[0].username == "buyer"


@respx.mock
async def test_the_poll_offset_and_timeout_are_sent_to_telegram() -> None:
    route = respx.post(f"{BASE}/bot{TOKEN}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": []})
    )

    await client().updates(TOKEN, offset=42)

    body = json.loads(route.calls.last.request.read())
    assert body["offset"] == 42
    assert body["timeout"] == 1
    assert body["allowed_updates"] == ["message"]


@respx.mock
async def test_sending_returns_the_telegram_message_id() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})
    )

    assert await client().send(TOKEN, 555, "привет") == 99


@respx.mock
async def test_a_second_poller_on_the_same_token_is_a_conflict() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/getUpdates").mock(
        return_value=httpx.Response(409, json={"ok": False, "description": "terminated by other getUpdates request"})
    )

    with pytest.raises(TelegramConflictError):
        await client().updates(TOKEN, offset=0)


@respx.mock
async def test_rate_limits_and_outages_are_temporary_while_rejections_are_not() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})
    )
    with pytest.raises(TelegramTemporaryError):
        await client().send(TOKEN, 555, "привет")

    respx.post(f"{BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(403, json={"ok": False, "description": "bot was blocked by the user"})
    )
    with pytest.raises(TelegramPermanentError):
        await client().send(TOKEN, 555, "привет")


@respx.mock
async def test_a_rate_limit_carries_telegrams_retry_after() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(
            429,
            json={"ok": False, "description": "Too Many Requests: retry after 17", "parameters": {"retry_after": 17}},
        )
    )

    with pytest.raises(TelegramRateLimitError) as excinfo:
        await client().send(TOKEN, 555, "привет")

    assert excinfo.value.retry_after == 17


@respx.mock
async def test_a_network_failure_is_temporary() -> None:
    respx.post(f"{BASE}/bot{TOKEN}/getMe").mock(side_effect=httpx.ConnectError("no route"))

    with pytest.raises(TelegramTemporaryError):
        await client().me(TOKEN)
