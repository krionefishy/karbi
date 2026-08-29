import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete, func, select

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import (
    DRIFT,
    FAILED,
    VERIFIED,
    FbsDistributionEnrollment,
    FbsDistributionService,
    NothingToPublishError,
    PublicationService,
    WriteNotAllowedError,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    ProductMappingModel,
    PublishedStockModel,
    StockPublicationModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient, WBFbsStockWriter
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
WAREHOUSE = 777
STOCKS = f"/api/v3/stocks/{WAREHOUSE}"
SKU = "2000000000017"
CHRT = 5001


@pytest.fixture
def stub() -> Iterator[EgressStub]:
    with respx.mock as router:
        yield EgressStub(router)


@pytest_asyncio.fixture
async def cabinet() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    seller = SellerModel(name="Публикация тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(seller)
        await session.flush()
        await session.commit()
    try:
        yield database, seller.id
    finally:
        async with database.session() as session:
            await FbsDistributionRepository(session).purge_seller(seller.id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


def publisher(session) -> PublicationService:
    return PublicationService(
        session,
        SellerRepository(session),
        FbsDistributionRepository(session),
        WBFbsMarketplaceClient(make_gateway()),
        WBFbsStockWriter(make_gateway()),
    )


def writes(stub: EgressStub) -> list[dict]:
    return [call for call in stub.requests_to(STOCKS) if call["method"] == "PUT"]


async def prepare(database, seller_id: uuid.UUID, amount: int, *, write: bool = True) -> None:
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        session.add(
            ProductMappingModel(
                seller_id=seller_id,
                chrt_id=CHRT,
                item_id="A1",
                characteristic="",
                barcode=SKU,
                article="101",
            )
        )
        await session.commit()
    if write:
        async with database.session() as session:
            await FbsDistributionService(
                session, SellerRepository(session), FbsDistributionRepository(session)
            ).set_write_enabled(seller_id, True)
    async with database.session() as session:
        await FbsDistributionRepository(session).save_plan(
            seller_id=seller_id,
            snapshot_id=None,
            created_at=NOW,
            reserve_units=20,
            priority_regions=3,
            warehouses=1,
            items=[(CHRT, {WAREHOUSE: amount})],
            skips=[],
        )
        await session.commit()


async def test_a_cabinet_without_permission_is_never_written_to(cabinet, stub) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id, 10, write=False)
    stub.on("PUT", STOCKS, status=204)

    async with database.session() as session:
        with pytest.raises(WriteNotAllowedError):
            await publisher(session).publish(seller_id, now=NOW)

    assert stub.calls == []


async def test_the_stock_is_written_by_barcode_not_by_size_id(cabinet, stub) -> None:
    """WB does not validate field names: a body keyed by chrtId returns 204 and
    changes nothing. The mistake would never surface as an error."""
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on("POST", STOCKS, body={"stocks": [{"sku": SKU, "chrtId": CHRT, "amount": 10}]})

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    [write] = writes(stub)
    assert write["body"] == {"stocks": [{"sku": SKU, "amount": 10}]}
    # Конверт несёт селлера — ключ подставляет шлюз.
    assert write["seller_id"] == str(seller_id)
    assert [outcome.status for outcome in result.outcomes] == [VERIFIED]


async def test_a_number_that_did_not_change_is_not_sent_again(cabinet, stub) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on("POST", STOCKS, body={"stocks": [{"sku": SKU, "chrtId": CHRT, "amount": 10}]})
    async with database.session() as session:
        await publisher(session).publish(seller_id, now=NOW)

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert len(writes(stub)) == 1
    assert result.outcomes == []


async def test_a_pair_dropped_from_the_plan_goes_out_as_zero(cabinet, stub) -> None:
    """Otherwise goods that were sold or withdrawn stay on sale in WB."""
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on(
        "POST",
        STOCKS,
        side_effect=[
            (200, {"stocks": [{"sku": SKU, "chrtId": CHRT, "amount": 10}]}),
            (200, {"stocks": []}),
        ],
    )
    async with database.session() as session:
        await publisher(session).publish(seller_id, now=NOW)

    # Новый план без этой пары вовсе.
    async with database.session() as session:
        await FbsDistributionRepository(session).save_plan(
            seller_id=seller_id,
            snapshot_id=None,
            created_at=NOW,
            reserve_units=20,
            priority_regions=3,
            warehouses=1,
            items=[],
            skips=[],
        )
        await session.commit()

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert writes(stub)[-1]["body"] == {"stocks": [{"sku": SKU, "amount": 0}]}
    assert [outcome.status for outcome in result.outcomes] == [VERIFIED]


async def test_a_successful_answer_is_not_taken_as_a_published_number(cabinet, stub) -> None:
    """204 with the stock unchanged is exactly the failure WB warns about, and
    it must show up as drift rather than as success."""
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on("POST", STOCKS, body={"stocks": [{"sku": SKU, "chrtId": CHRT, "amount": 3}]})

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert [outcome.status for outcome in result.outcomes] == [DRIFT]
    assert result.drift == 1
    async with database.session() as session:
        stored = await FbsDistributionRepository(session).published(seller_id)
    # Запоминается то, что WB подтвердил, а не то, что мы отправили.
    assert stored == {(WAREHOUSE, SKU): 3}


async def test_a_missing_row_in_the_answer_counts_as_zero(cabinet, stub) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on("POST", STOCKS, body={"stocks": []})

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert result.drift == 1
    async with database.session() as session:
        assert await FbsDistributionRepository(session).published(seller_id) == {(WAREHOUSE, SKU): 0}


async def test_a_refused_write_is_recorded_and_nothing_is_confirmed(cabinet, stub) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=409)

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert [outcome.status for outcome in result.outcomes] == [FAILED]
    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert await distribution.published(seller_id) == {}
        [record] = await distribution.publication_history(seller_id)
    assert record.status == FAILED and record.error


async def test_publishing_without_a_plan_is_refused(cabinet, stub) -> None:
    database, seller_id = cabinet
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await FbsDistributionService(
            session, SellerRepository(session), FbsDistributionRepository(session)
        ).set_write_enabled(seller_id, True)

    async with database.session() as session:
        with pytest.raises(NothingToPublishError):
            await publisher(session).publish(seller_id, now=NOW)


async def test_a_size_without_a_barcode_is_not_published(cabinet, stub) -> None:
    """Stock is written by sku; a size with no barcode has nowhere to go."""
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    async with database.session() as session:
        await session.execute(delete(ProductMappingModel).where(ProductMappingModel.seller_id == seller_id))
        session.add(
            ProductMappingModel(
                seller_id=seller_id, chrt_id=CHRT, item_id="A1", characteristic="", barcode="", article="101"
            )
        )
        await session.commit()
    stub.on("PUT", STOCKS, status=204)

    async with database.session() as session:
        result = await publisher(session).publish(seller_id, now=NOW)

    assert writes(stub) == []
    assert result.outcomes == []


async def test_purging_a_cabinet_forgets_what_was_published(cabinet, stub) -> None:
    database, seller_id = cabinet
    await prepare(database, seller_id, 10)
    stub.on("PUT", STOCKS, status=204)
    stub.on("POST", STOCKS, body={"stocks": [{"sku": SKU, "chrtId": CHRT, "amount": 10}]})
    async with database.session() as session:
        await publisher(session).publish(seller_id, now=NOW)

    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).purge(seller_id)
        await session.commit()

    async with database.session() as session:
        assert await session.get(PublishedStockModel, (seller_id, WAREHOUSE, SKU)) is None
        assert await FbsDistributionRepository(session).publication_history(seller_id) == []
        left = await session.scalar(
            select(func.count()).select_from(StockPublicationModel).where(StockPublicationModel.seller_id == seller_id)
        )
    assert left == 0
