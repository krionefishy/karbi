from datetime import date, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_turnover.infrastructure.wb import WBMarketplaceClient, WBStatisticsClient
from backend.modules.wb_turnover.infrastructure.wb.base import ATTEMPTS

STATISTICS = "https://statistics-api.wildberries.ru"
MARKETPLACE = "https://marketplace-api.wildberries.ru"
KEY = "wb-key"


@respx.mock
async def test_stock_rows_are_read_per_warehouse_and_barcode() -> None:
    respx.get(f"{STATISTICS}/api/v1/supplier/stocks").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"nmId": 101, "barcode": "b1", "warehouseName": "Коледино", "quantity": 5, "quantityFull": 7},
                {"nmId": 101, "barcode": "b1", "warehouseName": "Казань", "quantity": 3, "quantityFull": 3},
                {"barcode": "b2", "quantity": 9},
            ],
        )
    )

    rows = await WBStatisticsClient().stocks(KEY)

    # Rows stay per warehouse — the caller sums them; a row without nmId is dropped.
    assert [(row.article, row.quantity, row.warehouse_name) for row in rows] == [
        ("101", 5, "Коледино"),
        ("101", 3, "Казань"),
    ]


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
async def test_fbs_stock_is_asked_for_in_chunks_of_a_thousand_barcodes() -> None:
    route = respx.post(f"{MARKETPLACE}/api/v3/stocks/7").mock(
        return_value=httpx.Response(200, json={"stocks": [{"sku": "b1", "amount": 4}]})
    )

    amounts = await WBMarketplaceClient().stocks(KEY, 7, [f"b{index}" for index in range(1500)])

    assert len(route.calls) == 2
    assert amounts == {"b1": 4}


@respx.mock
async def test_a_rejected_key_is_permanent_while_an_outage_is_temporary(monkeypatch) -> None:
    # The retry backoff is the point of the loop, not of this test.
    monkeypatch.setattr("backend.modules.wb_turnover.infrastructure.wb.base.asyncio.sleep", AsyncMock())
    respx.get(f"{STATISTICS}/api/v1/supplier/stocks").mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(WBPermanentError):
        await WBStatisticsClient().stocks(KEY)

    outage = respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(WBTemporaryError):
        await WBMarketplaceClient().warehouses(KEY)
    assert len(outage.calls) == ATTEMPTS
