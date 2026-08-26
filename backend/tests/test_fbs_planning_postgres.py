import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy import update as sa_update

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import (
    ArticleModel,
    CredentialModel,
    OutboxEventModel,
    SellerModel,
)
from backend.modules.wb_fbs_distribution.application import (
    MANUAL,
    FbsDistributionEnrollment,
    MappingService,
    MirrorService,
    PlacementService,
    PlanningService,
    SnapshotService,
)
from backend.modules.wb_fbs_distribution.application.planning import (
    NO_STOCK,
    POOL_SPLIT_MISSING,
    SHARES_MISSING,
)
from backend.modules.wb_fbs_distribution.domain import DEFAULT_REGIONS, StockLine, StockSnapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    AllocationItemModel,
    AllocationPlanModel,
    AllocationSkipModel,
    FbsDistributionRepository,
    OfficeRegionModel,
    PoolSellerShareModel,
    ProductMappingModel,
    RegionModel,
    SellerWarehouseModel,
    StockPoolModel,
    StockSnapshotModel,
    WBOfficeModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import Office, SellerWarehouse, WBFbsMarketplaceClient
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
CODES = [code for code, _ in DEFAULT_REGIONS]
SHARES = dict(zip(CODES, (4000, 2000, 1200, 1300, 1000, 500), strict=True))
# Один склад в каждом из шести направлений: очередь короткая и порог низкий.
CITIES = {"moscow": 101, "volga": 102, "krasnodar": 103, "ural": 104, "northwest": 105, "novosibirsk": 106}


class FakeMarketplace(WBFbsMarketplaceClient):
    def __init__(self, offices, warehouses) -> None:
        super().__init__()
        self.office_rows, self.warehouse_rows = list(offices), list(warehouses)

    async def offices(self, api_key: str):
        return list(self.office_rows)

    async def warehouses(self, api_key: str):
        return list(self.warehouse_rows)


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


@pytest_asyncio.fixture
async def cabinet() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    seller = SellerModel(name="План тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(seller)
        await session.flush()
        session.add(
            CredentialModel(
                seller_id=seller.id,
                encrypted_api_key=cipher().encrypt("wb-plan-key"),
                key_fingerprint=uuid.uuid4().hex,
            )
        )
        session.add_all(
            [
                ArticleModel(
                    seller_id=seller.id,
                    article="101",
                    vendor_code="SKU-101",
                    name="Товар",
                    sizes=[{"chrt_id": 5001, "tech_size": "0", "skus": ["2000000000017"]}],
                    state="active",
                )
            ]
        )
        await session.commit()

    # Всё, что дальше, — тоже подготовка, но она уже может упасть. Держим её
    # внутри try, иначе падение setup оставит селлера следующему тесту.
    try:
        yield database, seller.id
    finally:
        async with database.session() as session:
            for model in (
                AllocationItemModel,
                AllocationSkipModel,
                AllocationPlanModel,
                ProductMappingModel,
                PoolSellerShareModel,
                StockPoolModel,
                StockSnapshotModel,
                SellerWarehouseModel,
                WBOfficeModel,
                OfficeRegionModel,
            ):
                await session.execute(delete(model))
            for position, (code, _) in enumerate(DEFAULT_REGIONS):
                await session.execute(
                    sa_update(RegionModel).where(RegionModel.code == code).values(position=position, share_bp=0)
                )
            await FbsDistributionRepository(session).save_settings(reserve_units=20, priority_regions=3)
            await FbsDistributionRepository(session).purge_seller(seller.id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


async def prepare(database: Database, seller_id: uuid.UUID) -> None:
    offices = [
        Office(
            office_id=office_id,
            name=code,
            city=code,
            address="адрес",
            federal_district="округ",
            longitude=None,
            latitude=None,
            cargo_type=1,
            delivery_type=1,
            selected=True,
        )
        for code, office_id in CITIES.items()
    ]
    warehouses = [
        SellerWarehouse(
            warehouse_id=office_id * 10,
            office_id=office_id,
            store_id=None,
            name=f"Склад {code}",
            cargo_type=1,
            delivery_type=1,
            is_deleting=False,
            is_processing=False,
        )
        for code, office_id in CITIES.items()
    ]
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await MirrorService(
            session,
            SellerRepository(session),
            FbsDistributionRepository(session),
            FakeMarketplace(offices, warehouses),
            cipher(),
        ).sync_seller(seller_id)
    async with database.session() as session:
        placement = PlacementService(session, FbsDistributionRepository(session))
        for code, office_id in CITIES.items():
            await placement.assign_office(office_id, code)
        await placement.save_regions([(code, SHARES[code]) for code in CODES])


async def stock(database, quantity: int) -> None:
    async with database.session() as session:
        await SnapshotService(session, FbsDistributionRepository(session), max_age_minutes=60).accept(
            StockSnapshot(
                generated_at=NOW,
                lines=(
                    StockLine(
                        item_id="A1", barcode="2000000000017", name="Товар", characteristic="", quantity=quantity
                    ),
                ),
            ),
            source=MANUAL,
            now=NOW,
        )


async def match(database, seller_id: uuid.UUID) -> None:
    async with database.session() as session:
        await MappingService(session, SellerRepository(session), FbsDistributionRepository(session)).rematch(seller_id)


async def build(database, seller_id: uuid.UUID):
    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        return await PlanningService(session, distribution, PlacementService(session, distribution)).build(
            seller_id, now=NOW
        )


async def test_a_plan_never_promises_more_than_is_available(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    await stock(database, 100)
    await match(database, seller_id)

    plan = await build(database, seller_id)

    [item] = plan.items
    assert (item.on_hand, item.available) == (100, 80)
    assert item.units == 80
    assert sum(item.amounts.values()) == item.available


async def test_a_small_stock_reaches_only_the_first_directions(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    # На остатке не больше резерва он не берётся вовсе: доступно все 4.
    await stock(database, 4)
    await match(database, seller_id)

    plan = await build(database, seller_id)

    [item] = plan.items
    assert item.available == 4
    assert sorted(item.amounts.values(), reverse=True) == [2, 1, 1]
    assert len(item.amounts) == 3


async def test_the_plan_is_written_down_with_the_snapshot_it_came_from(cabinet) -> None:
    """Without the input a published number cannot be explained months later."""
    database, seller_id = cabinet
    await prepare(database, seller_id)
    await stock(database, 100)
    await match(database, seller_id)

    plan = await build(database, seller_id)

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        stored = await distribution.latest_plan(seller_id)
        items = await distribution.plan_items(plan.id)
    assert stored is not None
    assert stored.snapshot_id == (await snapshot_id(database))
    assert stored.units == 80
    assert sum(row.amount for row in items) == 80


async def snapshot_id(database) -> uuid.UUID:
    async with database.session() as session:
        header = await FbsDistributionRepository(session).latest_snapshot()
    assert header is not None
    return header.id


async def test_an_item_left_with_nothing_after_the_reserve_is_skipped_with_a_reason(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    await stock(database, 0)
    await match(database, seller_id)

    plan = await build(database, seller_id)

    assert plan.items == []
    assert [skip.reason for skip in plan.skips] == [NO_STOCK]


async def test_full_coverage_without_shares_is_reported_not_guessed(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    async with database.session() as session:
        await PlacementService(session, FbsDistributionRepository(session)).save_regions([(code, 0) for code in CODES])
    await stock(database, 100)
    await match(database, seller_id)

    plan = await build(database, seller_id)

    assert [skip.reason for skip in plan.skips] == [SHARES_MISSING]
    assert plan.items == []


async def test_a_warehouse_wb_is_still_creating_is_left_out(cabinet) -> None:
    """Publishing onto it would be refused, and counting it in would hand its
    share to a warehouse that cannot take stock."""
    database, seller_id = cabinet
    await prepare(database, seller_id)
    await stock(database, 100)
    await match(database, seller_id)
    async with database.session() as session:
        await session.execute(
            sa_update(SellerWarehouseModel)
            .where(SellerWarehouseModel.warehouse_id == CITIES["moscow"] * 10)
            .values(is_processing=True)
        )
        await session.commit()

    plan = await build(database, seller_id)

    assert plan.warehouses == len(CITIES) - 1
    assert CITIES["moscow"] * 10 not in plan.items[0].amounts
    assert sum(plan.items[0].amounts.values()) == 80


async def test_a_pool_shared_without_a_rule_is_skipped(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    await stock(database, 100)
    await match(database, seller_id)
    # Второй кабинет расходует тот же пул, правила деления нет.
    async with database.session() as session:
        session.add(
            ProductMappingModel(
                seller_id=uuid.uuid4(),
                chrt_id=9001,
                item_id="A1",
                characteristic="",
                barcode="2000000000017",
                article="999",
            )
        )
        await session.commit()

    plan = await build(database, seller_id)

    assert [skip.reason for skip in plan.skips] == [POOL_SPLIT_MISSING]
    assert plan.items == []


async def test_a_shared_pool_with_a_rule_gets_only_its_share(cabinet) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id)
    other = uuid.uuid4()
    await stock(database, 100)
    await match(database, seller_id)
    async with database.session() as session:
        session.add(
            ProductMappingModel(
                seller_id=other,
                chrt_id=9001,
                item_id="A1",
                characteristic="",
                barcode="2000000000017",
                article="999",
            )
        )
        await session.commit()
    async with database.session() as session:
        await MappingService(session, SellerRepository(session), FbsDistributionRepository(session)).set_shares(
            "A1", "", {seller_id: 6000, other: 4000}
        )

    plan = await build(database, seller_id)

    # 100 - 20 = 80 доступно на пул, кабинету достаётся 60% = 48.
    [item] = plan.items
    assert item.available == 48
    assert sum(item.amounts.values()) == 48
