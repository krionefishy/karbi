import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBChatClient, WBPermanentError
from backend.tests.egress_stub import EgressStub, make_gateway

EVENTS = "/api/v1/seller/events"
SELLER = "seller-1"

# Поля и их типы взяты из настоящего ответа WB; подпись ответа укорочена.
PROMPT_ROW = {
    "chatID": "1:93a20fa0-ab27-0ffe-302c-b0bafa29ab2c",
    "eventID": "01aba0dc-5b5b-4f0c-bfaf-7dbeed7e0d81",
    "eventType": "message",
    "isNewChat": True,
    "message": {
        "attachments": {
            "goodCard": {
                "nmID": 1304195061,
                "price": 0,
                "priceCurrency": "",
                "rid": "eAP.m80165aef86ce46c3ab9e2292cb04f3ae.0.0",
                "size": "",
                "name": "Пылесос автомобильный аккумуляторный",
            }
        },
        "text": "Здравствуйте. Вы оставили отзыв с низкой оценкой. Давайте обсудим, что не так с товаром. "
        "Пожалуйста, расскажите подробно: попробую решить проблему",
    },
    "source": "seller-portal",
    "addTimestamp": 1789652126866,
    "addTime": "2026-09-17T13:35:26Z",
    "replySign": "1:93a20fa0",
    "sender": "seller",
}
FOLLOW_UP_ROW = {
    "chatID": "1:93a20fa0-ab27-0ffe-302c-b0bafa29ab2c",
    "eventID": "bf583a60-5a1b-4046-a5d9-0c529c893017",
    "eventType": "message",
    "message": {"text": "Сообщение выше автоматическое и ответа не требует."},
    "source": "seller-public-api",
    "addTimestamp": 1789652173452,
    "addTime": "2026-09-17T13:36:13Z",
    "replySign": "1:93a20fa0",
    "sender": "seller",
}
PHOTO_ROW = {
    "chatID": "1:af9d8fbc-0527-7d93-eea6-b4c3e5325620",
    "eventID": "98f49a1b-ba73-4a70-8dc6-1011b3d2cbea",
    "eventType": "message",
    "message": {"attachments": {"images": [{"date": "0001-01-01T00:00:00Z", "url": "", "downloadID": "b183f612"}]}},
    "source": "ios",
    "addTimestamp": 1789653403087,
    "addTime": "2026-09-17T13:56:43Z",
    "sender": "client",
    "clientName": "Екатерина",
}


def feed(rows: list[dict], cursor: int) -> dict:
    return {"result": {"next": cursor, "totalEvents": len(rows), "events": rows}, "errors": None}


def client() -> WBChatClient:
    return WBChatClient(make_gateway())


async def test_the_wb_review_prompt_is_told_from_our_follow_up() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", EVENTS, body=feed([PROMPT_ROW, FOLLOW_UP_ROW, PHOTO_ROW], 1789653403087))

        page = await client().events(SELLER, after=1789651800000)

        envelope = stub.calls[0]
        assert (envelope["api"], envelope["query"]) == ("chat", {"next": 1789651800000})

    prompt, follow_up, photo = page.events
    assert page.next == 1789653403087
    assert prompt.review_prompt and prompt.is_new_chat and prompt.nm_id == 1304195061
    assert prompt.added_at.isoformat() == "2026-09-17T13:35:26.866000+00:00"
    assert not follow_up.review_prompt and follow_up.source == "seller-public-api"
    assert photo.text is None and photo.has_attachments and photo.sender == "client"


async def test_the_first_read_sends_no_cursor_and_an_empty_feed_is_not_an_error() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", EVENTS, body={"result": {"next": 0, "totalEvents": 0, "events": []}, "errors": None})

        page = await client().events(SELLER, after=None)

        assert stub.calls[0]["query"] is None
    assert page.events == [] and page.next is None


async def test_a_key_without_the_chat_category_says_so() -> None:
    with respx.mock as router:
        EgressStub(router).on("GET", EVENTS, status=401, body={"title": "unauthorized"})

        with pytest.raises(WBPermanentError, match="Чат с покупателями"):
            await client().events(SELLER, after=None)


async def test_an_answer_of_an_unexpected_shape_is_a_permanent_error() -> None:
    with respx.mock as router:
        EgressStub(router).on("GET", EVENTS, body={"result": {"events": "нет"}})

        with pytest.raises(WBPermanentError, match="неожиданном виде"):
            await client().events(SELLER, after=None)
