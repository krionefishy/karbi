import pytest
import respx

from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_support_bot.infrastructure.wb import WBChatSender
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "11111111-1111-1111-1111-111111111111"
MESSAGE = "/api/v1/seller/message"


async def test_the_message_goes_out_once_as_form_fields() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", MESSAGE, body={"result": {"addTime": 1, "chatID": "1:c"}, "errors": []})

        await WBChatSender(make_gateway()).send(SELLER, "1:sign", "Добрый день!")

        envelope = stub.calls[0]
        assert envelope["form"] == {"replySign": "1:sign", "message": "Добрый день!"}
        assert (envelope["api"], envelope["method"], envelope["body"]) == ("chat", "POST", None)


@pytest.mark.parametrize("status", [401, 403])
async def test_access_refusals_keep_the_wb_diagnostics_without_guessing_token_permissions(status: int) -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            MESSAGE,
            status=status,
            body={"title": "forbidden", "detail": "good card: check rid error", "requestId": "wb-request-123"},
        )

        with pytest.raises(WBPermanentError, match=f"HTTP {status}") as caught:
            await WBChatSender(make_gateway()).send(SELLER, "1:sign", "Добрый день!")

        message = str(caught.value)
        assert "good card: check rid error" in message and "wb-request-123" in message
        assert "отказ доступа" in message and "Чат с покупателями" in message
        assert "Чтение и запись" not in message
        assert len(stub.calls) == 1


async def test_access_errors_do_not_expose_credentials_or_unrelated_response_fields() -> None:
    token = ".".join(["a" * 40, "b" * 40, "c" * 40])
    with respx.mock as router:
        EgressStub(router).on(
            "POST",
            MESSAGE,
            status=403,
            body={
                "title": "forbidden",
                "requestId": "wb-request-123",
                "detail": f"token={token}\nAuthorization: Bearer secret-value replySign=private-sign",
                "api_key": "private-key",
                "messages": ["private buyer message"],
            },
        )
        with pytest.raises(WBPermanentError) as caught:
            await WBChatSender(make_gateway()).send(SELLER, "1:sign", "Добрый день!")

        message = str(caught.value)
        assert "wb-request-123" in message and "[redacted]" in message
        assert all(secret not in message for secret in (token, "secret-value", "private-sign", "private-key"))
        assert "private buyer message" not in message and "\n" not in message


@pytest.mark.parametrize("body", [None, "unauthorized"])
async def test_access_errors_also_handle_empty_and_non_json_bodies(body: str | None) -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", MESSAGE, status=401, body=body)
        with pytest.raises(WBPermanentError, match="HTTP 401"):
            await WBChatSender(make_gateway()).send(SELLER, "1:sign", "Добрый день!")


async def test_empty_and_overlong_texts_never_reach_the_gateway() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        sender = WBChatSender(make_gateway())

        with pytest.raises(WBPermanentError):
            await sender.send(SELLER, "1:sign", "   ")
        with pytest.raises(WBPermanentError):
            await sender.send(SELLER, "1:sign", "а" * 1001)

        assert stub.calls == []


async def test_chat_signs_come_from_the_chat_list() -> None:
    rows = [
        {"chatID": "1:a", "replySign": "1:a:sig-a", "clientName": "Имя"},
        {"chatID": "1:b", "replySign": "1:b:sig-b"},
        {"chatID": "1:c"},
    ]
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", "/api/v1/seller/chats", body={"result": rows, "errors": None})

        signs = await WBChatSender(make_gateway()).reply_signs(SELLER)

        assert signs == {"1:a": "1:a:sig-a", "1:b": "1:b:sig-b"}
        assert stub.calls[0]["method"] == "GET" and stub.calls[0]["api"] == "chat"
