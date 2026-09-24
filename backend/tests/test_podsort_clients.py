from datetime import date

import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError, WBWarehouseRemainsClient
from backend.modules.wb_podsort.infrastructure.wb import WBPodsortStatisticsClient
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"
ORDERS = "/api/v1/supplier/orders"
REMAINS = "/api/v1/warehouse_remains"


def order(srid: str, **overrides) -> dict:
    row = {
        "date": "2026-09-22T12:12:31",
        "lastChangeDate": "2026-09-24T12:53:25",
        "warehouseName": "Склад WB РФ",
        "warehouseType": "Склад WB",
        "countryName": "Россия",
        "oblastOkrugName": "Центральный федеральный округ",
        "regionName": "Москва",
        "supplierArticle": "KARBI - Бесщеточный шуруповерт ударный",
        "nmId": 1112466805,
        "barcode": "2051917005518",
        "subject": "Шуруповерты",
        "techSize": "0",
        "isCancel": False,
        "srid": srid,
    }
    row.update(overrides)
    return row


@respx.mock
async def test_orders_of_a_day_come_by_barcode_and_region() -> None:
    stub = EgressStub(respx.mock)
    stub.on(
        "GET",
        ORDERS,
        body=[
            order("a"),
            order("b", warehouseType="Склад продавца", isCancel=True),
            order("c", countryName="Беларусь", oblastOkrugName=""),
            # Повтор той же строки и чужой день не должны раздувать сутки.
            order("a"),
            order("d", date="2026-09-21T23:59:00"),
            order("e", barcode=""),
        ],
    )

    lines = await WBPodsortStatisticsClient(make_gateway()).orders_on(SELLER, date(2026, 9, 22))

    assert [(line.region, line.fbs) for line in lines] == [
        ("Центральный", False),
        ("Центральный", True),
        ("Беларусь", False),
    ]
    call = stub.requests_to(ORDERS)[0]
    assert (call["api"], call["query"]) == ("statistics", {"dateFrom": "2026-09-22", "flag": 1})


@respx.mock
async def test_remains_wait_for_the_report_and_keep_every_warehouse_line() -> None:
    stub = EgressStub(respx.mock)
    stub.on("GET", REMAINS, body={"data": {"taskId": "task-1"}})
    stub.on(
        "GET",
        f"{REMAINS}/tasks/task-1/status",
        side_effect=[(200, {"data": {"status": "processing"}}), (200, {"data": {"status": "done"}})],
    )
    stub.on(
        "GET",
        f"{REMAINS}/tasks/task-1/download",
        body=[
            {
                "vendorCode": "KARBI - Электрощетка зеленая",
                "nmId": 1248473004,
                "barcode": "2053237557494",
                "techSize": "0",
                "warehouses": [
                    {"warehouseName": "В пути до получателей", "quantity": 171},
                    {"warehouseName": "Тула", "quantity": 490},
                    {"warehouseName": "Коледино", "quantity": 8},
                ],
            },
            {"nmId": 1, "warehouses": [{"warehouseName": "Тула", "quantity": 1}]},
        ],
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = WBWarehouseRemainsClient(make_gateway(), poll_seconds=5, sleep=sleep)
    remains = await client.remains(SELLER)

    assert [(item.warehouse_name, item.quantity) for item in remains] == [
        ("В пути до получателей", 171),
        ("Тула", 490),
        ("Коледино", 8),
    ]
    assert {item.barcode for item in remains} == {"2053237557494"}
    assert sleeps == [5]
    assert stub.requests_to(REMAINS)[0]["query"]["groupByBarcode"] == "true"


@respx.mock
async def test_remains_that_never_get_ready_are_a_temporary_error() -> None:
    stub = EgressStub(respx.mock)
    stub.on("GET", REMAINS, body={"data": {"taskId": "task-2"}})
    stub.on("GET", f"{REMAINS}/tasks/task-2/status", body={"data": {"status": "processing"}})

    # Опрос статуса у шлюза идёт дольше паузы: срок считается по часам, а не по сумме пауз.
    moment = [0.0]

    async def sleep(seconds: float) -> None:
        moment[0] += seconds + 20

    client = WBWarehouseRemainsClient(
        make_gateway(), poll_seconds=5, max_wait_seconds=60, sleep=sleep, clock=lambda: moment[0]
    )
    with pytest.raises(WBTemporaryError):
        await client.remains(SELLER)
    assert len(stub.requests_to(f"{REMAINS}/tasks/task-2/status")) == 4


@respx.mock
async def test_remains_without_a_task_id_are_a_permanent_error() -> None:
    stub = EgressStub(respx.mock)
    stub.on("GET", REMAINS, body={"data": {}})

    with pytest.raises(WBPermanentError):
        await WBWarehouseRemainsClient(make_gateway()).remains(SELLER)
