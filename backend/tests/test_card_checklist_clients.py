from datetime import UTC, datetime

import pytest
import respx

from backend.modules.wb_card_checklist.infrastructure.wb import WBCardClient, WBPricesClient
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.tests.egress_stub import EgressStub, make_gateway

CARDS = "/content/v2/get/cards/list"
PRICES = "/api/v2/list/goods/filter"
SELLER = "seller-1"


def raw_card(nm_id: int, **overrides) -> dict:
    card = {
        "nmID": nm_id,
        "imtID": 3577925540,
        "subjectID": 2224,
        "subjectName": "Электропилы цепные",
        "vendorCode": "Пила аккумуляторная",
        "title": "Пила аккумуляторная цепная садовая",
        "description": "  Мощная пила для сада.  ",
        "video": "https://videonme-basket-07.wbbasket.ru/vol74/index.m3u8",
        "photos": [
            {"big": "https://basket/1/big.webp", "c246x328": "https://basket/1/small.webp"},
            {"big": "https://basket/2/big.webp"},
        ],
        "characteristics": [
            {"id": 385630, "name": "Длина шины", "value": ['6"', '8"']},
            {"id": 73899, "name": "Объем масляного бака", "value": 0.15},
            {"id": 4370, "name": "Материал корпуса", "value": []},
        ],
        "sizes": [{"chrtID": 1, "techSize": "0", "skus": ["", "2054000592278"]}],
        "createdAt": "2026-07-29T06:56:14.3383931Z",
    }
    card.update(overrides)
    return card


async def test_cards_keep_what_the_checklist_checks() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            CARDS,
            body={
                "cards": [raw_card(1304367626), raw_card(1445837491, video=None, photos=[], description="")],
                "cursor": {"updatedAt": "2026-09-10T07:31:40Z", "nmID": 1445837491, "total": 2},
            },
        )

        first, second = await WBCardClient(make_gateway()).cards(SELLER)

    assert first.article == "1304367626"
    assert first.title == "Пила аккумуляторная цепная садовая"
    assert first.barcode == "2054000592278"
    assert (first.photo_count, first.photo_url) == (2, "https://basket/1/small.webp")
    assert first.description_length == len("Мощная пила для сада.")
    assert first.has_video
    # Пустое значение — это незаполненная характеристика, а не заполненная пустотой.
    assert first.characteristic_ids == frozenset({385630, 73899})
    assert first.card_created_at == datetime(2026, 7, 29, 6, 56, 14, 338393, tzinfo=UTC)
    assert (second.has_video, second.photo_count, second.description_length) == (False, 0, 0)


async def test_characteristics_directory_is_read_per_subject() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            "/content/v2/object/charcs/2224",
            body={
                "data": [
                    {"charcID": 4370, "name": "Материал корпуса", "required": False, "popular": True},
                    {"charcID": 14177452, "name": "Описание", "existNamedField": True},
                ],
                "error": False,
            },
        )

        directory = await WBCardClient(make_gateway()).characteristics(SELLER, 2224)

        assert stub.calls[0]["api"] == "content"

    assert [(item.charc_id, item.popular, item.named_field) for item in directory] == [
        (4370, True, False),
        (14177452, False, True),
    ]


async def test_prices_are_read_page_by_page(monkeypatch) -> None:
    monkeypatch.setattr("backend.modules.wb_card_checklist.infrastructure.wb.client.PRICES_PAGE_LIMIT", 2)

    def goods(nm_id: int, discount: int = 95) -> dict:
        return {
            "nmID": nm_id,
            "sizes": [
                {"price": 179999, "discountedPrice": 8999.95},
                {"price": 179999, "discountedPrice": 8899.95},
            ],
            "discount": discount,
            "clubDiscount": 4,
        }

    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            PRICES,
            side_effect=[
                (200, {"data": {"listGoods": [goods(1), goods(2, discount=0)]}}),
                (200, {"data": {"listGoods": [goods(3)]}}),
            ],
        )

        prices = await WBPricesClient(make_gateway()).prices(SELLER)

        calls = stub.requests_to(PRICES)
        assert [call["query"]["offset"] for call in calls] == ["0", "2"]
        assert {call["api"] for call in calls} == {"prices"}

    assert [price.article for price in prices] == ["1", "2", "3"]
    # На витрине покупатель видит самую низкую цену из размеров.
    assert (prices[0].price, prices[0].discounted_price, prices[0].discount) == (179999, 8899.95, 95)
    assert prices[1].discount == 0


async def test_a_refused_price_list_is_permanent() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", PRICES, body={"data": None, "error": True, "errorText": "нет доступа"})

        with pytest.raises(WBPermanentError, match="нет доступа"):
            await WBPricesClient(make_gateway()).prices(SELLER)
