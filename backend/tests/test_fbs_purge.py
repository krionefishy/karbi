import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import delete, func, select

from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import FbsDistributionEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    AllocationItemModel,
    AllocationSkipModel,
    FbsDistributionRepository,
    PoolSellerShareModel,
    ProductMappingModel,
    PublishedStockModel,
    SellerWarehouseModel,
    StockPublicationModel,
    WBFbsDistributionBase,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    db = Database()
    await db.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield db
    finally:
        await db.disconnect()


async def test_the_module_leaves_nothing_behind_after_a_purge(database) -> None:
    """The registry calls purge and cannot know what we keep inside.

    The check walks the schema instead of a hand-written list: a table added
    later and forgotten in `purge_seller` fails here rather than quietly
    outliving the cabinet it belonged to.
    """
    seller = SellerModel(name="Очистка тест", catalog_sync_status="success")
    plan_id, other_plan = uuid.uuid4(), uuid.uuid4()
    async with database.session() as session:
        session.add(seller)
        await session.flush()
        seller_id = seller.id
        distribution = FbsDistributionRepository(session)
        await distribution.track(seller_id)
        session.add_all(
            [
                SellerWarehouseModel(seller_id=seller_id, warehouse_id=1, office_id=1, name="Склад"),
                ProductMappingModel(
                    seller_id=seller_id, chrt_id=1, item_id="A1", characteristic="", barcode="200", article="1"
                ),
                PoolSellerShareModel(item_id="A1", characteristic="", seller_id=seller_id, share_bp=10_000),
                PublishedStockModel(seller_id=seller_id, warehouse_id=1, sku="200", amount=5),
                StockPublicationModel(
                    id=uuid.uuid4(), seller_id=seller_id, plan_id=plan_id, warehouse_id=1, rows=1, status="verified"
                ),
            ]
        )
        await distribution.save_plan(
            seller_id=seller_id,
            snapshot_id=None,
            created_at=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
            reserve_units=20,
            priority_regions=3,
            warehouses=1,
            items=[(1, {1: 5})],
            skips=[(2, "A2", "", "no_stock")],
        )
        await session.commit()

    async with database.session() as session:
        plans = await FbsDistributionRepository(session).latest_plan(seller_id)
        assert plans is not None
        other_plan = plans.id

    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).purge(seller_id)
        await session.commit()

    try:
        async with database.session() as session:
            for table in WBFbsDistributionBase.metadata.tables.values():
                if "seller_id" not in table.columns:
                    continue
                left = await session.scalar(
                    select(func.count()).select_from(table).where(table.c.seller_id == seller_id)
                )
                assert left == 0, f"{table.name} пережила очистку кабинета"
            # Строки плана привязаны к нему, а не к селлеру: их метаданный
            # обход выше не увидит, поэтому проверяются отдельно.
            for model in (AllocationItemModel, AllocationSkipModel):
                left = await session.scalar(select(func.count()).select_from(model).where(model.plan_id == other_plan))
                assert left == 0, f"{model.__tablename__} пережила очистку кабинета"
    finally:
        async with database.session() as session:
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller_id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()
