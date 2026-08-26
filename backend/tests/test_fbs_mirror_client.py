import httpx
import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient

MARKETPLACE = "https://marketplace-api.wildberries.ru"
KEY = "wb-key"

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


@respx.mock
async def test_offices_are_read_with_the_fields_the_plan_needs() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE_ROW]))

    [office] = await WBFbsMarketplaceClient().offices(KEY)

    assert (office.office_id, office.city, office.cargo_type) == (10236, "Абакан", 1)
    assert office.federal_district == "Сибирский федеральный округ"
    assert (office.longitude, office.latitude) == (91.37692, 53.71977)
    assert office.selected is False


@respx.mock
async def test_an_office_without_a_federal_district_still_arrives() -> None:
    """WB leaves the district null for a fifth of the offices; dropping them
    would quietly shrink the catalogue the operator picks from."""
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(
        return_value=httpx.Response(200, json=[{**OFFICE_ROW, "federalDistrict": None}])
    )

    [office] = await WBFbsMarketplaceClient().offices(KEY)

    assert office.federal_district == ""


@respx.mock
async def test_a_row_without_an_identifier_is_skipped() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(
        return_value=httpx.Response(200, json=[OFFICE_ROW, {"name": "Без id", "city": "Москва"}])
    )

    offices = await WBFbsMarketplaceClient().offices(KEY)

    assert [office.office_id for office in offices] == [10236]


@respx.mock
async def test_warehouses_keep_the_office_they_are_bound_to() -> None:
    """`officeId` is what the stock is physically handed over to; losing it
    would leave a warehouse nobody can deliver for."""
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(200, json=[WAREHOUSE_ROW]))

    [warehouse] = await WBFbsMarketplaceClient().warehouses(KEY)

    assert (warehouse.warehouse_id, warehouse.office_id, warehouse.store_id) == (2035130, 3103350, 50214336)
    assert warehouse.name == "1. Москва Домодедово"
    assert (warehouse.is_processing, warehouse.is_deleting) == (False, False)


@respx.mock
async def test_a_warehouse_being_created_is_reported_as_such() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(
        return_value=httpx.Response(200, json=[{**WAREHOUSE_ROW, "isProcessing": True}])
    )

    [warehouse] = await WBFbsMarketplaceClient().warehouses(KEY)

    assert warehouse.is_processing is True


@respx.mock
async def test_an_answer_that_is_not_a_list_is_a_permanent_error() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(200, json={"error": "nope"}))

    with pytest.raises(WBPermanentError):
        await WBFbsMarketplaceClient().warehouses(KEY)


@respx.mock
async def test_a_key_without_the_marketplace_category_says_so() -> None:
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(403))

    with pytest.raises(WBPermanentError, match="Маркетплейс"):
        await WBFbsMarketplaceClient().offices(KEY)
