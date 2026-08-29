import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.tests.egress_stub import EgressStub, make_gateway

OFFICES = "/api/v3/offices"
WAREHOUSES = "/api/v3/warehouses"
SELLER = "seller-1"

# Ответы урезаны до одной строки, но поля и их типы взяты из настоящего ответа WB.
OFFICE_ROW = {
    "federalDistrict": "Сибирский федеральный округ",
    "address": "РФ, Республика Хакасия, г. Абакан, ул. Складская 11",
    "name": "Абакан-2",
    "city": "Абакан",
    "id": 10236,
    "longitude": 91.37692,
    "latitude": 53.71977,
    "cargoType": 1,
    "deliveryType": 1,
    "selected": False,
}
WAREHOUSE_ROW = {
    "name": "1. Москва Домодедово",
    "officeId": 3103350,
    "storeId": 50214336,
    "id": 2035130,
    "cargoType": 1,
    "deliveryType": 1,
    "isDeleting": False,
    "isProcessing": False,
}


def client() -> WBFbsMarketplaceClient:
    return WBFbsMarketplaceClient(make_gateway())


async def test_offices_are_read_with_the_fields_the_plan_needs() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", OFFICES, body=[OFFICE_ROW])

        [office] = await client().offices(SELLER)

        # Конверт шлюза несёт селлера и раздел WB — ключа в нём нет.
        envelope = stub.calls[0]
        assert envelope["seller_id"] == SELLER and envelope["api"] == "marketplace"

    assert (office.office_id, office.city, office.cargo_type) == (10236, "Абакан", 1)
    assert office.federal_district == "Сибирский федеральный округ"
    assert (office.longitude, office.latitude) == (91.37692, 53.71977)
    assert office.selected is False


async def test_an_office_without_a_federal_district_still_arrives() -> None:
    """WB leaves the district null for a fifth of the offices; dropping them
    would quietly shrink the catalogue the operator picks from."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", OFFICES, body=[{**OFFICE_ROW, "federalDistrict": None}])

        [office] = await client().offices(SELLER)

    assert office.federal_district == ""


async def test_a_row_without_an_identifier_is_skipped() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", OFFICES, body=[OFFICE_ROW, {"name": "Без id", "city": "Москва"}])

        offices = await client().offices(SELLER)

    assert [office.office_id for office in offices] == [10236]


async def test_warehouses_keep_the_office_they_are_bound_to() -> None:
    """`officeId` is what the stock is physically handed over to; losing it
    would leave a warehouse nobody can deliver for."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", WAREHOUSES, body=[WAREHOUSE_ROW])

        [warehouse] = await client().warehouses(SELLER)

    assert (warehouse.warehouse_id, warehouse.office_id, warehouse.store_id) == (2035130, 3103350, 50214336)
    assert warehouse.name == "1. Москва Домодедово"
    assert (warehouse.is_processing, warehouse.is_deleting) == (False, False)


async def test_a_warehouse_being_created_is_reported_as_such() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", WAREHOUSES, body=[{**WAREHOUSE_ROW, "isProcessing": True}])

        [warehouse] = await client().warehouses(SELLER)

    assert warehouse.is_processing is True


async def test_an_answer_that_is_not_a_list_is_a_permanent_error() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", WAREHOUSES, body={"error": "nope"})

        with pytest.raises(WBPermanentError):
            await client().warehouses(SELLER)


async def test_a_key_without_the_marketplace_category_says_so() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", OFFICES, status=403)

        with pytest.raises(WBPermanentError, match="Маркетплейс"):
            await client().offices(SELLER)
