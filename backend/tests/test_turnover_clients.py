from datetime import date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_core.infrastructure.wb.egress import ATTEMPTS
from backend.modules.wb_turnover.infrastructure.wb import (
    PAGE_LIMIT,
    WBAnalyticsClient,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.tests.egress_stub import EgressStub, make_gateway

ORDERS = "/api/v1/supplier/orders"
WAREHOUSES = "/api/v3/warehouses"
STOCKS_REPORT = "/api/analytics/v1/stocks-report/wb-warehouses"
SELLER = "seller-1"


async def test_orders_without_srid_are_skipped() -> None:
    """Without srid the same order cannot be recognised twice, and counting it
    again is worse than not counting it."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            ORDERS,
            body=[
                {
                    "srid": "s1",
                    "nmId": 101,
                    "date": "2026-08-19T10:00:00",
                    "lastChangeDate": "2026-08-19T12:00:00",
                    "isCancel": False,
                    "finishedPrice": 1200,
                    "warehouseType": "Склад WB",
                },
                {"nmId": 102, "date": "2026-08-19T10:00:00", "lastChangeDate": "2026-08-19T10:00:00"},
            ],
        )

        rows = await WBStatisticsClient(make_gateway()).orders(SELLER, datetime(2026, 8, 6, 0, 0))

    assert len(rows) == 1
    assert (rows[0].srid, rows[0].article, rows[0].order_date) == ("s1", "101", date(2026, 8, 19))
    assert rows[0].price == 1200


async def test_the_orders_window_is_sent_the_way_wb_expects_it() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", ORDERS, body=[])

        await WBStatisticsClient(make_gateway()).orders(SELLER, datetime(2026, 8, 6, 7, 30))

    envelope = stub.requests_to(ORDERS)[-1]
    assert envelope["query"] == {"dateFrom": "2026-08-06T07:30:00", "flag": 0}
    # Ключа в конверте нет — шлюз подставит его сам по селлеру.
    assert envelope["seller_id"] == SELLER
    assert envelope["api"] == "statistics"


async def test_fbs_stock_is_asked_by_size_in_chunks_of_a_thousand() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", "/api/v3/stocks/7", body={"stocks": [{"sku": "b1", "chrtId": 11, "amount": 4}]})

        amounts = await WBMarketplaceClient(make_gateway()).stocks(SELLER, 7, list(range(1500)))

    calls = stub.requests_to("/api/v3/stocks/7")
    assert len(calls) == 2
    assert calls[0]["body"]["chrtIds"][:3] == [0, 1, 2]
    assert "skus" not in calls[0]["body"]
    assert amounts == {11: 4}


async def test_warehouses_being_deleted_are_left_out() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            WAREHOUSES,
            body=[
                {"id": 1, "name": "Москва"},
                {"id": 2, "name": "Уезжает", "isDeleting": True},
                {"name": "Без id"},
            ],
        )

        warehouses = await WBMarketplaceClient(make_gateway()).warehouses(SELLER)

    assert [(w.id, w.name) for w in warehouses] == [(1, "Москва")]


def item(nm_id: int, quantity: int, **extra) -> dict:
    row = {
        "nmId": nm_id,
        "chrtId": nm_id + 1,
        "warehouseId": -999999,
        "warehouseName": "Склад WB",
        "regionName": "Склад WB",
        "quantity": quantity,
        "inWayToClient": 0,
        "inWayFromClient": 0,
    }
    row.update(extra)
    return row


def report(items: list[dict]) -> tuple[int, dict]:
    return 200, {"data": {"items": items}}


async def test_the_stock_report_is_asked_for_as_wb_expects() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, body={"data": {"items": []}})

        await WBAnalyticsClient(make_gateway()).stocks(SELLER)

    envelope = stub.requests_to(STOCKS_REPORT)[-1]
    assert envelope["method"] == "POST"
    assert envelope["seller_id"] == SELLER
    assert envelope["api"] == "analytics"
    assert envelope["body"] == {"limit": PAGE_LIMIT, "offset": 0}


async def test_stock_lines_become_rows_and_missing_numbers_become_zero() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST", STOCKS_REPORT, body={"data": {"items": [item(101, 5, inWayToClient=7), {"nmId": 102, "chrtId": 3}]}}
        )

        rows = await WBAnalyticsClient(make_gateway()).stocks(SELLER)

    assert [(r.article, r.quantity, r.in_way_to_client) for r in rows] == [("101", 5, 7), ("102", 0, 0)]


async def test_a_line_without_nmid_is_dropped_not_guessed() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, body={"data": {"items": [item(101, 5), {"chrtId": 9, "quantity": 3}]}})

        rows = await WBAnalyticsClient(make_gateway()).stocks(SELLER)

    assert [r.article for r in rows] == ["101"]


async def test_a_full_page_is_followed_by_the_next_one() -> None:
    """A short page means the report is over; a full one never does."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            STOCKS_REPORT,
            side_effect=[
                report([item(index, 1) for index in range(PAGE_LIMIT)]),
                report([item(999, 1)]),
            ],
        )

        rows = await WBAnalyticsClient(make_gateway()).stocks(SELLER)

    calls = stub.requests_to(STOCKS_REPORT)
    assert len(rows) == PAGE_LIMIT + 1
    assert calls[1]["body"]["offset"] == PAGE_LIMIT


async def test_an_empty_report_is_a_success_not_an_error() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, body={"data": {"items": []}})

        assert await WBAnalyticsClient(make_gateway()).stocks(SELLER) == []


async def test_an_unexpected_report_shape_is_permanent() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, body={"data": {"items": "нет"}})

        with pytest.raises(WBPermanentError):
            await WBAnalyticsClient(make_gateway()).stocks(SELLER)


async def test_a_key_without_the_analytics_category_says_so() -> None:
    """«Неверный ключ» sends someone to reissue a key that is in fact fine."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, status=403)

        with pytest.raises(WBPermanentError, match="Аналитика"):
            await WBAnalyticsClient(make_gateway()).stocks(SELLER)


async def test_a_wb_rate_limit_delivered_by_the_gateway_is_final(monkeypatch) -> None:
    """Шлюз уже отработал свои ретраи: 429 в конверте окончателен с первого раза."""
    sleep = AsyncMock()
    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.egress.asyncio.sleep", sleep)
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", STOCKS_REPORT, status=429)

        with pytest.raises(WBTemporaryError):
            await WBAnalyticsClient(make_gateway()).stocks(SELLER)
        assert len(stub.requests_to(STOCKS_REPORT)) == 1
    assert sleep.await_count == 0


async def test_a_transport_failure_is_retried_before_giving_up(monkeypatch) -> None:
    # The retry backoff is the point of the loop, not of this test.
    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.egress.asyncio.sleep", AsyncMock())
    with respx.mock as router:
        stub = EgressStub(router)

        def reply(payload: dict) -> tuple[int, dict]:
            raise httpx.ConnectError("соединение оборвалось")

        stub.on("POST", STOCKS_REPORT, reply=reply)

        with pytest.raises(WBTemporaryError):
            await WBAnalyticsClient(make_gateway()).stocks(SELLER)
        assert len(stub.requests_to(STOCKS_REPORT)) == ATTEMPTS


async def test_a_rejected_key_is_permanent_while_an_outage_is_temporary() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", ORDERS, status=401)
        with pytest.raises(WBPermanentError, match="Статистика"):
            await WBStatisticsClient(make_gateway()).orders(SELLER, datetime(2026, 8, 6))

        # 5xx от WB тоже доносится шлюзом как окончательный: без локальных повторов.
        stub.on("GET", WAREHOUSES, status=503)
        with pytest.raises(WBTemporaryError):
            await WBMarketplaceClient(make_gateway()).warehouses(SELLER)
        assert len(stub.requests_to(WAREHOUSES)) == 1
