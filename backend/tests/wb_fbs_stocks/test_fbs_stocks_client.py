import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_stocks.infrastructure.wb import WBFbsStocksClient
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"
WAREHOUSE_ROW = {
    "name": "Фулэксперт СПБ ",
    "officeId": 10999,
    "storeId": 313451,
    "id": 1917658,
    "cargoType": 1,
    "deliveryType": 1,
    "isDeleting": False,
    "isProcessing": False,
}


async def test_warehouses_come_with_delivery_type_and_trimmed_names() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", "/api/v3/warehouses", body=[WAREHOUSE_ROW, {"name": "без id"}])

        [warehouse] = await WBFbsStocksClient(make_gateway()).warehouses(SELLER)

    assert (warehouse.warehouse_id, warehouse.office_id, warehouse.name) == (1917658, 10999, "Фулэксперт СПБ")
    assert warehouse.fbs and not warehouse.is_deleting
    assert stub.calls[0]["api"] == "marketplace"


async def test_stocks_are_asked_by_barcode_and_a_missing_row_is_not_a_zero() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            "/api/v3/stocks/1917658",
            body={"stocks": [{"sku": "2052957817499", "chrtId": 1800164566, "amount": 28}]},
        )

        amounts = await WBFbsStocksClient(make_gateway()).stocks(SELLER, 1917658, ["2052957817499", "2000000000001"])

    assert amounts == {"2052957817499": 28}
    assert stub.calls[0]["body"] == {"skus": ["2052957817499", "2000000000001"]}


async def test_a_warehouse_without_a_single_record_is_an_empty_page() -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", "/api/v3/stocks/1917658", body={"stocks": None})

        assert await WBFbsStocksClient(make_gateway()).stocks(SELLER, 1917658, ["2052957817499"]) == {}


async def test_an_answer_that_is_not_a_list_is_a_permanent_error() -> None:
    with respx.mock as router:
        EgressStub(router).on("GET", "/api/v3/warehouses", body={"error": True})

        with pytest.raises(WBPermanentError):
            await WBFbsStocksClient(make_gateway()).warehouses(SELLER)
