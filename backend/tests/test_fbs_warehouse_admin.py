import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete
from sqlalchemy import update as sa_update

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel, OutboxEventModel, SellerModel
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
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
MARKETPLACE = "https://marketplace-api.wildberries.ru"

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


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


@pytest_asyncio.fixture
async def cabinet() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    seller = SellerModel(name="Склады тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(seller)
        await session.flush()
        session.add(
            CredentialModel(
                seller_id=seller.id,
                encrypted_api_key=cipher().encrypt("wb-admin-key"),
                key_fingerprint=uuid.uuid4().hex,
            )
        )
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
        WBFbsMarketplaceClient(),
        WBFbsWarehouseWriter(),
        cipher(),
    )


async def enrol(database, seller_id: uuid.UUID, *, write: bool) -> None:
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    if write:
        async with database.session() as session:
            await FbsDistributionService(
                session, SellerRepository(session), FbsDistributionRepository(session)
            ).set_write_enabled(seller_id, True)


@respx.mock
async def test_a_cabinet_without_write_permission_is_never_touched(cabinet) -> None:
    """The permission is the only thing between a mistaken click and a live
    cabinet, so nothing may reach WB before it is granted."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=False)
    route = respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 1}))

    async with database.session() as session:
        with pytest.raises(WriteNotAllowedError):
            await admin(session).create(seller_id, 242, "Москва")

    assert route.call_count == 0


@respx.mock
async def test_creating_a_warehouse_mirrors_it_at_once(cabinet) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    create = respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 777}))
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE]))
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(200, json=[warehouse_row(777, 242)]))

    async with database.session() as session:
        created = await admin(session).create(seller_id, 242, "  Москва Вешки  ")

    assert created.warehouse_id == 777
    # Название уходит подчищенным от лишних пробелов, объект — числом.
    assert json.loads(create.calls.last.request.read()) == {"name": "Москва Вешки", "officeId": 242}
    async with database.session() as session:
        rows = await FbsDistributionRepository(session).warehouses(seller_id)
    assert [(row.warehouse_id, row.office_id) for row in rows] == [(777, 242)]


@respx.mock
async def test_a_second_warehouse_on_the_same_office_is_refused_before_wb_sees_it(cabinet) -> None:
    """WB forbids it anyway, but its refusal explains nothing to the operator."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE]))
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(200, json=[warehouse_row(777, 242)]))
    respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 778}))
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Первый")
    posts_before = len([call for call in respx.calls if call.request.method == "POST"])

    async with database.session() as session:
        with pytest.raises(WarehouseConflictError):
            await admin(session).create(seller_id, 242, "Второй")

    # Второй запрос до WB не дошёл: отказ выдан по зеркалу, до сети.
    assert len([call for call in respx.calls if call.request.method == "POST"]) == posts_before


@respx.mock
async def test_a_warehouse_still_in_the_scheme_is_not_deleted(cabinet) -> None:
    """Deleting it would leave the calculation counting on a warehouse that is
    gone, and WB does not bring deleted ones back."""
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE]))
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(200, json=[warehouse_row(777, 242)]))
    respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 777}))
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")

    removal = respx.delete(f"{MARKETPLACE}/api/v3/warehouses/777").mock(return_value=httpx.Response(204))
    async with database.session() as session:
        with pytest.raises(WarehouseConflictError, match="участвует"):
            await admin(session).delete(seller_id, 777)

    assert removal.call_count == 0


@respx.mock
async def test_a_warehouse_taken_out_of_the_scheme_can_be_deleted(cabinet) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE]))
    warehouses = respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(
        return_value=httpx.Response(200, json=[warehouse_row(777, 242)])
    )
    respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 777}))
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")
    async with database.session() as session:
        await session.execute(
            sa_update(SellerWarehouseModel).where(SellerWarehouseModel.warehouse_id == 777).values(participates=False)
        )
        await session.commit()

    removal = respx.delete(f"{MARKETPLACE}/api/v3/warehouses/777").mock(return_value=httpx.Response(204))
    warehouses.mock(return_value=httpx.Response(200, json=[]))
    async with database.session() as session:
        await admin(session).delete(seller_id, 777)

    assert removal.call_count == 1
    async with database.session() as session:
        assert await FbsDistributionRepository(session).warehouses(seller_id) == []


@respx.mock
async def test_rebinding_onto_a_busy_office_is_refused(cabinet) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    respx.get(f"{MARKETPLACE}/api/v3/offices").mock(return_value=httpx.Response(200, json=[OFFICE]))
    respx.get(f"{MARKETPLACE}/api/v3/warehouses").mock(
        return_value=httpx.Response(200, json=[warehouse_row(777, 242), warehouse_row(778, 204)])
    )
    respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"id": 777}))
    async with database.session() as session:
        await admin(session).create(seller_id, 242, "Москва")

    rename = respx.put(f"{MARKETPLACE}/api/v3/warehouses/778").mock(return_value=httpx.Response(204))
    async with database.session() as session:
        with pytest.raises(WarehouseConflictError):
            await admin(session).rebind(seller_id, 778, name="Переехал", office_id=242)

    assert rename.call_count == 0


@respx.mock
async def test_a_creation_answer_without_an_id_is_a_permanent_error(cabinet) -> None:
    database, seller_id = cabinet
    await enrol(database, seller_id, write=True)
    respx.post(f"{MARKETPLACE}/api/v3/warehouses").mock(return_value=httpx.Response(201, json={"ok": True}))

    async with database.session() as session:
        with pytest.raises(Exception, match="нет id"):
            await admin(session).create(seller_id, 242, "Москва")
