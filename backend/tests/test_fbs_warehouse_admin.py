import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete
from sqlalchemy import update as sa_update

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import (
    FbsDistributionEnrollment,
    FbsDistributionService,
    WarehouseAdminService,
    WarehouseConflictError,
    WriteNotAllowedError,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    SellerWarehouseModel,
    WBOfficeModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import (
    WBFbsMarketplaceClient,
    WBFbsWarehouseWriter,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
OFFICES = "/api/v3/offices"
WAREHOUSES = "/api/v3/warehouses"

OFFICE = {
    "federalDistrict": "Центральный федеральный округ",
    "address": "Москва",
    "name": "Москва (Вешки)",
    "city": "Москва",
    "id": 242,
    "longitude": 37.6,
    "latitude": 55.7,
    "cargoType": 1,
    "deliveryType": 1,
    "selected": False,
}


def warehouse_row(warehouse_id: int, office_id: int) -> dict:
    return {
        "name": f"Склад {warehouse_id}",
        "officeId": office_id,
        "storeId": warehouse_id * 10,
        "id": warehouse_id,
        "cargoType": 1,
        "deliveryType": 1,
        "isDeleting": False,
        "isProcessing": False,
    }


@pytest.fixture
def stub() -> Iterator[EgressStub]:
    with respx.mock as router:
        yield EgressStub(router)


@pytest_asyncio.fixture
async def cabinet() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    seller = SellerModel(name="Склады тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(seller)
        await session.flush()
        await session.commit()
    try:
        yield database, seller.id
    finally:
        async with database.session() as session:
            await session.execute(delete(WBOfficeModel))
            await FbsDistributionRepository(session).purge_seller(seller.id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


def admin(session) -> WarehouseAdminService:
    return WarehouseAdminService(
        session,
        SellerRepository(session),
        FbsDistributionRepository(session),
        WBFbsMarketplaceClient(make_gateway()),
        WBFbsWarehouseWriter(make_gateway()),
    )


def creations(stub: EgressStub) -> list[dict]:
    return [call for call in stub.requests_to(WAREHOUSES) if call["method"] == "POST"]


async def enrol(database, seller_id: uuid.UUID, *, write: bool) -> None:
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    if write:
        async with database.session() as session:
            await FbsDistributionService(
                session, SellerRepository(session), FbsDistributionRepository(session)
            ).set_write_enabled(seller_id, True)


async def test_a_cabinet_without_write_permission_is_never_touched(cabinet, stub) -> None:
    """The permission is the only thing between a mistaken click and a live
    cabinet, so nothing may reach WB before it is granted."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=False)
    stub.on("POST", WAREHOUSES, status=201, body={"id": 1})

    async with database.session() as session:
        with pytest.raises(WriteNotAllowedError):
            await admin(session).create(seller_id, 242, "Москва")

    assert stub.calls == []


async def test_creating_a_warehouse_mirrors_it_at_once(cabinet, stub) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("POST", WAREHOUSES, status=201, body={"id": 777})
    stub.on("GET", OFFICES, body=[OFFICE])
    stub.on("GET", WAREHOUSES, body=[warehouse_row(777, 242)])

    async with database.session() as session:
        created = await admin(session).create(seller_id, 242, "  Москва Вешки  ")

    assert created.warehouse_id == 777
    # Название уходит подчищенным от лишних пробелов, объект — числом.
    [create] = creations(stub)
    assert create["body"] == {"name": "Москва Вешки", "officeId": 242}
    # Конверт несёт селлера — ключ подставляет шлюз.
    assert create["seller_id"] == str(seller_id)
    async with database.session() as session:
        rows = await FbsDistributionRepository(session).warehouses(seller_id)
    assert [(row.warehouse_id, row.office_id) for row in rows] == [(777, 242)]


async def test_a_second_warehouse_on_the_same_office_is_refused_before_wb_sees_it(cabinet, stub) -> None:
    """WB forbids it anyway, but its refusal explains nothing to the operator."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("GET", OFFICES, body=[OFFICE])
    stub.on("GET", WAREHOUSES, body=[warehouse_row(777, 242)])
    stub.on("POST", WAREHOUSES, status=201, body={"id": 778})
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Первый")
    posts_before = len(creations(stub))

    async with database.session() as session:
        with pytest.raises(WarehouseConflictError):
            await admin(session).create(seller_id, 242, "Второй")

    # Второй запрос до WB не дошёл: отказ выдан по зеркалу, до сети.
    assert len(creations(stub)) == posts_before


async def test_a_warehouse_still_in_the_scheme_is_not_deleted(cabinet, stub) -> None:
    """Deleting it would leave the calculation counting on a warehouse that is
    gone, and WB does not bring deleted ones back."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("GET", OFFICES, body=[OFFICE])
    stub.on("GET", WAREHOUSES, body=[warehouse_row(777, 242)])
    stub.on("POST", WAREHOUSES, status=201, body={"id": 777})
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")

    stub.on("DELETE", f"{WAREHOUSES}/777", status=204)
    async with database.session() as session:
        with pytest.raises(WarehouseConflictError, match="участвует"):
            await admin(session).delete(seller_id, 777)

    assert stub.requests_to(f"{WAREHOUSES}/777") == []


async def test_a_warehouse_taken_out_of_the_scheme_can_be_deleted(cabinet, stub) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("GET", OFFICES, body=[OFFICE])
    wb_warehouses = [warehouse_row(777, 242)]
    stub.on("GET", WAREHOUSES, reply=lambda payload: (200, list(wb_warehouses)))
    stub.on("POST", WAREHOUSES, status=201, body={"id": 777})
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")
    async with database.session() as session:
        await session.execute(
            sa_update(SellerWarehouseModel).where(SellerWarehouseModel.warehouse_id == 777).values(participates=False)
        )
        await session.commit()

    stub.on("DELETE", f"{WAREHOUSES}/777", status=204)
    wb_warehouses.clear()
    async with database.session() as session:
        await admin(session).delete(seller_id, 777)

    assert len(stub.requests_to(f"{WAREHOUSES}/777")) == 1
    async with database.session() as session:
        assert await FbsDistributionRepository(session).warehouses(seller_id) == []


async def test_rebinding_onto_a_busy_office_is_refused(cabinet, stub) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("GET", OFFICES, body=[OFFICE])
    stub.on("GET", WAREHOUSES, body=[warehouse_row(777, 242), warehouse_row(778, 204)])
    stub.on("POST", WAREHOUSES, status=201, body={"id": 777})
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")

    stub.on("PUT", f"{WAREHOUSES}/778", status=204)
    async with database.session() as session:
        with pytest.raises(WarehouseConflictError):
            await admin(session).rebind(seller_id, 778, name="Переехал", office_id=242)

    assert stub.requests_to(f"{WAREHOUSES}/778") == []


async def test_a_creation_answer_without_an_id_is_a_permanent_error(cabinet, stub) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    stub.on("POST", WAREHOUSES, status=201, body={"ok": True})

    async with database.session() as session:
        with pytest.raises(Exception, match="нет id"):
            await admin(session).create(seller_id, 242, "Москва")
