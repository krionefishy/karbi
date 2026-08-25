import json
from datetime import date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_turnover.infrastructure.wb import (
    PAGE_LIMIT,
    WBAnalyticsClient,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.modules.wb_turnover.infrastructure.wb.base import ATTEMPTS

STATISTICS = "https://statistics-api.wildberries.ru"
MARKETPLACE = "https://marketplace-api.wildberries.ru"
KEY = "wb-key"


@respx.mock
async def test_orders_without_srid_are_skipped() -> None:
    """Without srid the same order cannot be recognised twice, and counting it
    again is worse than not counting it."""
    respx.get(f"{STATISTICS}/api/v1/supplier/orders").mock(
        return_value=httpx.Response(
            200,
            json=[
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
    )

    rows = await WBStatisticsClient().orders(KEY, datetime(2026, 8, 6, 0, 0))

    assert len(rows) == 1
    assert (rows[0].srid, rows[0].article, rows[0].order_date) == ("s1", "101", date(2026, 8, 19))
    assert rows[0].price == 1200


@respx.mock
async def test_the_orders_window_is_sent_the_way_wb_expects_it() -> None:
    route = respx.get(f"{STATISTICS}/api/v1/supplier/orders").mock(return_value=httpx.Response(200, json=[]))

    await WBStatisticsClient().orders(KEY, datetime(2026, 8, 6, 7, 30))

    assert route.calls.last.request.url.params["dateFrom"] == "2026-08-06T07:30:00"
    assert route.calls.last.request.url.params["flag"] == "0"
    assert route.calls.last.request.headers["Authorization"] == KEY


@respx.mock
async def test_fbs_stock_is_asked_by_size_in_chunks_of_a_thousand() -> None:
    route = respx.post(f"{MARKETPLACE}/api/v3/stocks/7").mock(
        return_value=httpx.Response(200, json={"stocks": [{"sku": "b1", "chrtId": 11, "amount": 4}]})
    )

    amounts = await WBMarketplaceClient().stocks(KEY, 7, list(range(1500)))

    assert len(route.calls) == 2
    assert json.loads(route.calls[0].request.read())["chrtIds"][:3] == [0, 1, 2]
    assert "skus" not in json.loads(route.calls[0].request.read())
    assert amounts == {11: 4}


@respx.mock
async def test_warehouses_being_deleted_are_left_out() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "name": "Москва"},
                {"id": 2, "name": "Уезжает", "isDeleting": True},
                {"name": "Без id"},
            ],
        )
    )

    assert [(w.id, w.name) for w in await WBMarketplaceClient().warehouses(KEY)] == [(1, "Москва")]


ANALYTICS = "https://seller-analytics-api.wildberries.ru"
STOCKS_REPORT = f"{ANALYTICS}/api/analytics/v1/stocks-report/wb-warehouses"


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


def report(items: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"data": {"items": items}})


@respx.mock
async def test_the_stock_report_is_asked_for_as_wb_expects() -> None:
    route = respx.post(STOCKS_REPORT).mock(return_value=report([]))

    await WBAnalyticsClient().stocks(KEY)

    request = route.calls.last.request
    assert request.method == "POST"
    assert request.headers["Authorization"] == KEY
    assert json.loads(request.read()) == {"limit": PAGE_LIMIT, "offset": 0}


@respx.mock
async def test_stock_lines_become_rows_and_missing_numbers_become_zero() -> None:
    respx.post(STOCKS_REPORT).mock(return_value=report([item(101, 5, inWayToClient=7), {"nmId": 102, "chrtId": 3}]))

    rows = await WBAnalyticsClient().stocks(KEY)

    assert [(r.article, r.quantity, r.in_way_to_client) for r in rows] == [("101", 5, 7), ("102", 0, 0)]


@respx.mock
async def test_a_line_without_nmid_is_dropped_not_guessed() -> None:
    respx.post(STOCKS_REPORT).mock(return_value=report([item(101, 5), {"chrtId": 9, "quantity": 3}]))

    rows = await WBAnalyticsClient().stocks(KEY)

    assert [r.article for r in rows] == ["101"]


@respx.mock
async def test_a_full_page_is_followed_by_the_next_one() -> None:
    """A short page means the report is over; a full one never does."""
    pages = [report([item(index, 1) for index in range(PAGE_LIMIT)]), report([item(999, 1)])]
    route = respx.post(STOCKS_REPORT).mock(side_effect=pages)

    rows = await WBAnalyticsClient().stocks(KEY)

    assert len(rows) == PAGE_LIMIT + 1
    assert json.loads(route.calls[1].request.read())["offset"] == PAGE_LIMIT


@respx.mock
async def test_an_empty_report_is_a_success_not_an_error() -> None:
    respx.post(STOCKS_REPORT).mock(return_value=httpx.Response(200, json={"data": {"items": []}}))

    assert await WBAnalyticsClient().stocks(KEY) == []


@respx.mock
async def test_an_unexpected_report_shape_is_permanent() -> None:
    respx.post(STOCKS_REPORT).mock(return_value=httpx.Response(200, json={"data": {"items": "нет"}}))

    with pytest.raises(WBPermanentError):
        await WBAnalyticsClient().stocks(KEY)


@respx.mock
async def test_a_key_without_the_analytics_category_says_so(monkeypatch) -> None:
    """«Неверный ключ» sends someone to reissue a key that is in fact fine."""
    monkeypatch.setattr("backend.modules.wb_turnover.infrastructure.wb.base.asyncio.sleep", AsyncMock())
    respx.post(STOCKS_REPORT).mock(return_value=httpx.Response(403, json={}))

    with pytest.raises(WBPermanentError, match="Аналитика"):
        await WBAnalyticsClient().stocks(KEY)


@respx.mock
async def test_the_report_is_retried_while_wb_is_unwell(monkeypatch) -> None:
    monkeypatch.setattr("backend.modules.wb_turnover.infrastructure.wb.base.asyncio.sleep", AsyncMock())
    route = respx.post(STOCKS_REPORT).mock(return_value=httpx.Response(429, json={}))

    with pytest.raises(WBTemporaryError):
        await WBAnalyticsClient().stocks(KEY)
    assert len(route.calls) == ATTEMPTS


@respx.mock
async def test_a_rejected_key_is_permanent_while_an_outage_is_temporary(monkeypatch) -> None:
    # The retry backoff is the point of the loop, not of this test.
    monkeypatch.setattr("backend.modules.wb_turnover.infrastructure.wb.base.asyncio.sleep", AsyncMock())
    respx.get(f"{STATISTICS}/api/v1/supplier/orders").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(WBPermanentError, match="Статистика"):
        await WBStatisticsClient().orders(KEY, datetime(2026, 8, 6))

    outage = respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(WBTemporaryError):
        await WBMarketplaceClient().warehouses(KEY)
    assert len(outage.calls) == ATTEMPTS
