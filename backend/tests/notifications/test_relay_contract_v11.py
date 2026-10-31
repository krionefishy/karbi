"""Клиент релея по контракту v1.1: новые поля апдейта, кнопки, правка, ответ на нажатие, файлы.

Обычное сообщение уходит тем же телом, что и в v1; кнопки и ответ-цитата добавляются, только когда они есть.
"""

import asyncio
import dataclasses
import json

import httpx
import pytest
import respx
from httpx import Response

from backend.modules.notifications.domain import (
    UPDATE_CALLBACK,
    UPDATE_MESSAGE,
    MessengerFileTooLargeError,
    MessengerPermanentError,
    MessengerTemporaryError,
)
from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.shared.settings import RelayConfig

CONFIG = RelayConfig(
    base_url="https://relay.invalid",
    jwt_secret="a-relay-secret-that-is-long-enough-for-tests",
    verify="false",
)
SEND = "https://relay.invalid/api/v1/messages/send"
EDIT = "https://relay.invalid/api/v1/messages/edit"
ANSWER = "https://relay.invalid/api/v1/callbacks/answer"
UPDATES = "https://relay.invalid/api/v1/updates"
FILE = "https://relay.invalid/api/v1/files/AbC_12-x"
BUTTONS = [[{"label": "Положительный", "action": "sc:o:AAAAAAAAAAAAAAAAAAAAAA:p"}]]


async def updates(items: list[dict]) -> list:
    with respx.mock:
        respx.get(UPDATES).mock(return_value=Response(200, json={"updates": items}))
        return await RelayClient(CONFIG).updates(bot_code="screens", after=0, wait=0)


class TestUpdates:
    async def test_a_v1_update_reads_as_a_plain_text_message(self) -> None:
        [item] = await updates(
            [{"bot_code": "b", "event_id": 5, "recipient": "555", "text": "/start x", "sender": {"external_id": "7"}}]
        )
        assert (item.kind, item.attachments, item.callback, item.caption, item.forwarded) == (
            UPDATE_MESSAGE,
            (),
            None,
            "",
            False,
        )
        assert item.text == "/start x"

    async def test_a_v11_photo_keeps_attachments_caption_and_message_ref(self) -> None:
        [item] = await updates(
            [
                {
                    "bot_code": "screens",
                    "event_id": 812345,
                    "recipient": "123456",
                    "text": "",
                    "sender": {"external_id": "777", "username": "ivan", "display_name": "Иван"},
                    "kind": "message",
                    "chat_type": "private",
                    "message_ref": "4521",
                    "media_group_id": "13562",
                    "caption": "полож",
                    "forwarded": True,
                    "attachments": [
                        {"kind": "photo", "file_ref": "ref1", "size": 482113, "width": 1280, "height": 720},
                        {"kind": "document", "file_ref": "ref2", "mime": "image/png", "file_name": "IMG_1.png"},
                        {"kind": "photo"},
                        "garbage",
                    ],
                    "unknown_future_field": {"x": 1},
                }
            ]
        )
        assert (item.chat_type, item.message_ref, item.media_group_id, item.caption, item.forwarded) == (
            "private",
            "4521",
            "13562",
            "полож",
            True,
        )
        assert [(a.kind, a.file_ref, a.size, a.mime) for a in item.attachments] == [
            ("photo", "ref1", 482113, ""),
            ("document", "ref2", None, "image/png"),
        ]

    async def test_a_callback_carries_its_id_data_and_message(self) -> None:
        [item] = await updates(
            [
                {
                    "event_id": 9,
                    "recipient": "123",
                    "text": "",
                    "kind": "callback",
                    "callback": {"id": "cb-1", "data": "sc:o:x:p", "message_ref": "4530"},
                }
            ]
        )
        assert item.kind == UPDATE_CALLBACK
        assert item.callback is not None
        assert (item.callback.id, item.callback.data, item.callback.message_ref) == ("cb-1", "sc:o:x:p", "4530")

    @pytest.mark.parametrize("payload", [{"kind": "callback"}, {"kind": "poll"}, {"kind": "callback", "callback": 1}])
    async def test_a_kind_this_side_cannot_use_degrades_to_a_message(self, payload: dict) -> None:
        [item] = await updates([{"event_id": 1, "recipient": "1", "text": "", **payload}])
        assert item.kind == UPDATE_MESSAGE
        assert item.callback is None


class TestSendingV11:
    async def test_buttons_and_reply_travel(self) -> None:
        with respx.mock:
            route = respx.post(SEND).mock(return_value=Response(200, json={"message_ref": "1"}))
            await RelayClient(CONFIG).send(
                bot_code="screens", recipient="1", text="t", idempotency_key="k", buttons=BUTTONS, reply_to="4521"
            )
        payload = json.loads(route.calls.last.request.content)
        assert payload["buttons"] == BUTTONS
        assert payload["reply_to"] == "4521"

    async def test_a_plain_message_is_the_v1_payload(self) -> None:
        with respx.mock:
            route = respx.post(SEND).mock(return_value=Response(200, json={"message_ref": "1"}))
            await RelayClient(CONFIG).send(bot_code="b", recipient="1", text="t", idempotency_key="k")
        assert set(json.loads(route.calls.last.request.content)) == {"bot_code", "recipient", "text", "idempotency_key"}

    async def test_edit_and_answer_go_to_their_endpoints(self) -> None:
        with respx.mock:
            edit = respx.post(EDIT).mock(return_value=Response(200, json={"status": "edited"}))
            answer = respx.post(ANSWER).mock(return_value=Response(200, json={"status": "answered"}))
            client = RelayClient(CONFIG)
            await client.edit_message(
                bot_code="screens", recipient="1", message_ref="4530", text="Исход", buttons=None, idempotency_key="e"
            )
            await client.answer_callback(bot_code="screens", callback_id="cb-1", text="Исход записан")
        assert json.loads(edit.calls.last.request.content) == {
            "bot_code": "screens",
            "recipient": "1",
            "message_ref": "4530",
            "text": "Исход",
            "buttons": None,
            "idempotency_key": "e",
        }
        assert json.loads(answer.calls.last.request.content) == {
            "bot_code": "screens",
            "callback_id": "cb-1",
            "alert": False,
            "text": "Исход записан",
        }

    async def test_a_too_old_press_is_permanent(self) -> None:
        with respx.mock:
            respx.post(ANSWER).mock(return_value=Response(422, json={"detail": "query is too old"}))
            with pytest.raises(MessengerPermanentError, match="too old"):
                await RelayClient(CONFIG).answer_callback(bot_code="screens", callback_id="cb-1")


class TestFiles:
    async def fetch(self, response: Response | Exception, max_bytes: int = 100) -> bytes:
        with respx.mock:
            if isinstance(response, Exception):
                respx.get(FILE).mock(side_effect=response)
            else:
                respx.get(FILE).mock(return_value=response)
            return await RelayClient(CONFIG).fetch_file(bot_code="screens", file_ref="AbC_12-x", max_bytes=max_bytes)

    async def test_the_bytes_come_back_and_the_limit_is_asked_for(self) -> None:
        with respx.mock:
            route = respx.get(FILE).mock(return_value=Response(200, content=b"\x89PNG-data"))
            data = await RelayClient(CONFIG).fetch_file(bot_code="screens", file_ref="AbC_12-x", max_bytes=100)
        assert data == b"\x89PNG-data"
        assert route.calls.last.request.url.params["max_bytes"] == "100"
        assert route.calls.last.request.url.params["bot_code"] == "screens"

    async def test_a_declared_size_over_the_limit_stops_before_reading(self) -> None:
        with pytest.raises(MessengerFileTooLargeError):
            await self.fetch(Response(200, content=b"x" * 10, headers={"Content-Length": "1000"}), max_bytes=100)

    async def test_a_body_growing_past_the_limit_is_cut_off(self) -> None:
        async def chunks():
            for _ in range(50):
                yield b"x" * 10

        with pytest.raises(MessengerFileTooLargeError):
            await self.fetch(Response(200, content=chunks()), max_bytes=100)

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (413, MessengerFileTooLargeError),
            (404, MessengerPermanentError),
            (410, MessengerPermanentError),
            (502, MessengerTemporaryError),
            (429, MessengerTemporaryError),
        ],
    )
    async def test_relay_answers_map_to_domain_errors(self, status: int, error: type[Exception]) -> None:
        with pytest.raises(error):
            await self.fetch(Response(status, json={"detail": "x"}))

    async def test_an_unreachable_relay_is_temporary(self) -> None:
        with pytest.raises(MessengerTemporaryError):
            await self.fetch(httpx.ConnectError("no route"))

    @pytest.mark.parametrize("file_ref", ["../bots", "a/b", "", "x" * 200, "a?b=c"])
    async def test_a_file_ref_outside_the_format_never_reaches_a_url(self, file_ref: str) -> None:
        with pytest.raises(MessengerPermanentError):
            await RelayClient(CONFIG).fetch_file(bot_code="screens", file_ref=file_ref, max_bytes=10)

    async def test_a_hanging_download_ends_on_the_deadline(self) -> None:
        async def slow(_: httpx.Request) -> Response:
            await asyncio.sleep(5)
            return Response(200, content=b"x")

        config = dataclasses.replace(CONFIG, file_timeout_seconds=0)
        with respx.mock:
            respx.get(FILE).mock(side_effect=slow)
            with pytest.raises(MessengerTemporaryError, match="timed out"):
                await RelayClient(config).fetch_file(bot_code="screens", file_ref="AbC_12-x", max_bytes=10)


class TestContractDetails:
    async def test_a_reply_to_that_is_not_a_message_number_is_not_sent(self) -> None:
        with respx.mock:
            route = respx.post(SEND).mock(return_value=Response(200, json={"message_ref": "1"}))
            await RelayClient(CONFIG).send(bot_code="b", recipient="1", text="t", idempotency_key="k", reply_to="abc")
        assert "reply_to" not in json.loads(route.calls.last.request.content)

    async def test_a_v11_update_with_nulls_reads_as_defaults(self) -> None:
        [item] = await updates(
            [
                {
                    "bot_code": "screens",
                    "event_id": 1,
                    "recipient": "1",
                    "text": "",
                    "kind": "message",
                    "reply_to_ref": None,
                    "media_group_id": None,
                    "caption": None,
                    "attachments": [
                        {"kind": "photo", "file_ref": "r", "size": None, "mime": None, "width": None, "file_name": None}
                    ],
                    "callback": None,
                }
            ]
        )
        assert (item.reply_to_ref, item.media_group_id, item.caption) == ("", "", "")
        [attachment] = item.attachments
        assert (attachment.size, attachment.mime, attachment.file_name) == (None, "", "")
