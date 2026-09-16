from datetime import UTC, datetime

import respx

from backend.modules.wb_core.domain import ORDER_SOURCE_ARCHIVE, ORDER_SOURCE_LIVE
from backend.modules.wb_core.infrastructure.wb import WBMarketplaceClient
from backend.tests.egress_stub import REQUEST_URL, EgressStub, make_gateway

SELLER = "seller-1"
ORDERS = "/api/v3/orders"
ARCHIVE = "/api/marketplace/v3/fbs/orders/archive"
SUPPLIES = "/api/v3/supplies"


def live_order(order_id: int, supply: str = "") -> dict:
    return {
        "id": order_id,
        "rid": f"eAK.r{order_id}.0.0",
        "orderUid": f"r{order_id}",
        "createdAt": "2026-09-10T00:28:00Z",
        "warehouseId": 2128014,
        "supplyId": supply,
        "officeId": 3089389,
        "nmId": 1304326830,
        "chrtId": 1927028410,
        "skus": ["2053999917338"],
        "price": 107300,
        "deliveryType": "fbs",
    }


async def test_orders_are_paged_by_cursor_and_split_into_30_day_windows() -> None:
    """WB отдаёт не больше 30 дней за запрос, и курсор `next` надо докручивать до нуля."""
    with respx.mock(assert_all_called=False) as router:
        stub = EgressStub(router)

        def reply(payload: dict) -> tuple[int, dict]:
            params = payload.get("query") or {}
            if int(params["next"]) == 0:
                return 200, {"orders": [live_order(1, "WB-GI-1")], "next": 777}
            return 200, {"orders": [live_order(2)], "next": 0}

        stub.on("GET", ORDERS, reply=reply)
        orders = await WBMarketplaceClient(make_gateway()).orders(
            SELLER, date_from=datetime(2026, 7, 1, tzinfo=UTC), date_to=datetime(2026, 8, 15, tzinfo=UTC)
        )

    # Два окна (30 дней + остаток), в каждом две страницы.
    windows = [call["query"]["dateFrom"] for call in stub.requests_to(ORDERS) if call["query"]["next"] == 0]
    assert len(windows) == 2
    assert [order.order_id for order in orders] == [1, 2, 1, 2]
    first = orders[0]
    assert (first.rid, first.supply_id, first.sku, first.source) == (
        "eAK.r1.0.0",
        "WB-GI-1",
        "2053999917338",
        ORDER_SOURCE_LIVE,
    )
    assert orders[1].supply_id is None
    assert first.created_at == datetime(2026, 9, 10, 0, 28, tzinfo=UTC)


async def test_archive_orders_carry_sticker_and_status() -> None:
    raw = {
        "id": 5314551443,
        "rid": "ez.i8240.0.0",
        "orderUid": "i8240",
        "createdAt": "2026-06-12",
        "warehouseId": 147672,
        "supplyId": "WB-GI-260259736",
        "stickerId": 33811984302,
        "status": {"supplierStatus": "complete", "wbStatus": "sold"},
        "product": {"article": "KARBI", "nmId": 1161692089, "chrtId": 1715758446, "skus": ["2052389488892"]},
        "priceInfo": {"price": 101000, "convertedPrice": 101000, "currencyCode": 643, "convertedCurrencyCode": 643},
    }
    with respx.mock(assert_all_called=False) as router:
        stub = EgressStub(router)
        stub.on("GET", ARCHIVE, body={"orders": [raw], "next": 0})
        orders = await WBMarketplaceClient(make_gateway()).archive_orders(SELLER, 2026, 6)

    assert stub.requests_to(ARCHIVE)[0]["query"] == {"year": 2026, "month": 6, "limit": 1000, "next": 0}
    [order] = orders
    assert (order.sticker_id, order.supplier_status, order.wb_status, order.source) == (
        33811984302,
        "complete",
        "sold",
        ORDER_SOURCE_ARCHIVE,
    )
    assert (order.nm_id, order.chrt_id, order.sku, order.price_kopecks) == (
        1161692089,
        1715758446,
        "2052389488892",
        101000,
    )
    assert order.created_at == datetime(2026, 6, 12, tzinfo=UTC)


async def test_supplies_walk_the_whole_list_and_keep_empty_dates_empty() -> None:
    raw = {
        "id": "WB-GI-260259736",
        "name": "Поставка от 31.07.2026",
        "createdAt": "2026-07-31T08:21:20Z",
        "closedAt": "2026-07-31T08:22:37Z",
        "scanDt": None,
        "destinationOfficeId": 15,
        "done": True,
        "cargoType": 1,
    }
    with respx.mock(assert_all_called=False) as router:
        stub = EgressStub(router)
        stub.on(
            "GET", SUPPLIES, side_effect=[(200, {"supplies": [raw], "next": 5}), (200, {"supplies": [], "next": 0})]
        )
        supplies = await WBMarketplaceClient(make_gateway()).supplies(SELLER)

    [supply] = supplies
    assert (supply.supply_id, supply.destination_office_id, supply.done, supply.scan_dt) == (
        "WB-GI-260259736",
        15,
        True,
        None,
    )
    assert supply.closed_at == datetime(2026, 7, 31, 8, 22, 37, tzinfo=UTC)
    assert len(stub.requests_to(SUPPLIES)) == 2


async def test_a_row_without_id_is_skipped_not_fatal() -> None:
    with respx.mock(assert_all_called=False) as router:
        stub = EgressStub(router)
        stub.on("GET", ORDERS, body={"orders": [{"rid": "x"}, live_order(3)], "next": 0})
        orders = await WBMarketplaceClient(make_gateway()).orders(
            SELLER, date_from=datetime(2026, 9, 1, tzinfo=UTC), date_to=datetime(2026, 9, 2, tzinfo=UTC)
        )
    assert [order.order_id for order in orders] == [3]
    assert REQUEST_URL  # заглушка отвечает за шлюз, прямых вызовов WB нет
