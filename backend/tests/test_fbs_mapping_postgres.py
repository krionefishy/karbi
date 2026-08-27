import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel, OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import (
    ONEC,
    InvalidShareError,
    MappingService,
    SnapshotService,
)
from backend.modules.wb_fbs_distribution.domain import BASIS_POINTS, StockLine, StockSnapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    PoolSellerShareModel,
    ProductMappingModel,
    StockPoolModel,
    StockSnapshotModel,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def card(seller_id: uuid.UUID, article: str, chrt_id: int, *barcodes: str) -> ArticleModel:
    return ArticleModel(
        seller_id=seller_id,
        article=article,
        vendor_code=f"SKU-{article}",
        name=f"Карточка {article}",
        sizes=[{"chrt_id": chrt_id, "tech_size": "0", "skus": list(barcodes)}],
        state="active",
    )


@pytest_asyncio.fixture
async def cabinets() -> AsyncIterator[tuple[Database, uuid.UUID, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    first = SellerModel(name="Кабинет один", catalog_sync_status="success")
    second = SellerModel(name="Кабинет два", catalog_sync_status="success")
    async with database.session() as session:
        session.add_all([first, second])
        await session.flush()
        session.add_all(
            [
                card(first.id, "101", 1001, "2000000000017"),
                card(first.id, "102", 1002, "2000000000024"),
                card(second.id, "201", 2001, "2000000000017"),
            ]
        )
        await session.commit()
    try:
        yield database, first.id, second.id
    finally:
        async with database.session() as session:
            for model in (ProductMappingModel, PoolSellerShareModel, StockPoolModel, StockSnapshotModel):
                await session.execute(delete(model))
            for seller_id in (first.id, second.id):
                await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller_id))
                await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()
        await database.disconnect()


async def load(database, *lines: StockLine) -> None:
    async with database.session() as session:
        service = SnapshotService(session, FbsDistributionRepository(session), max_age_minutes=60)
        await service.accept(StockSnapshot(generated_at=NOW, lines=tuple(lines)), source=ONEC, now=NOW)


def mapping(session) -> MappingService:
    return MappingService(session, SellerRepository(session), FbsDistributionRepository(session))


def line(item_id: str, barcode: str, quantity: int = 10) -> StockLine:
    return StockLine(item_id=item_id, barcode=barcode, name=f"Товар {item_id}", characteristic="", quantity=quantity)


async def test_a_pool_finds_the_size_that_carries_its_barcode(cabinets) -> None:
    database, first, _ = cabinets
    await load(database, line("A1", "2000000000017"), line("A2", "2000000000024"))

    async with database.session() as session:
        result = await mapping(session).rematch(first)

    assert result.matched == 2
    async with database.session() as session:
        rows = await FbsDistributionRepository(session).mappings(first)
    assert {row.item_id: row.chrt_id for row in rows} == {"A1": 1001, "A2": 1002}
    assert {row.article for row in rows} == {"101", "102"}


async def test_a_pool_without_a_card_is_reported_instead_of_published(cabinets) -> None:
    """A good that matched nothing must never reach WB silently."""
    database, first, _ = cabinets
    await load(database, line("A1", "2000000000017"), line("A9", "9999999999999", 42))

    async with database.session() as session:
        await mapping(session).rematch(first)
    async with database.session() as session:
        state = await mapping(session).state()

    assert state.mapped_pools == 1
    assert [(pool.item_id, pool.on_hand) for pool in state.unmapped] == [("A9", 42)]


async def test_rematching_drops_a_link_whose_side_disappeared(cabinets) -> None:
    """The catalogue and the snapshot both move; a link that lost one of them
    must vanish rather than point at nothing."""
    database, first, _ = cabinets
    await load(database, line("A1", "2000000000017"), line("A2", "2000000000024"))
    async with database.session() as session:
        await mapping(session).rematch(first)

    await load(database, line("A1", "2000000000017"))

    async with database.session() as session:
        await mapping(session).rematch(first)
    async with database.session() as session:
        rows = await FbsDistributionRepository(session).mappings(first)
    # Пул A2 обнулился, но остался; связь пропала вместе с его баркодом в снимке.
    assert [row.item_id for row in rows] == ["A1", "A2"]


async def test_one_barcode_in_two_cabinets_is_flagged_not_doubled(cabinets) -> None:
    """Both cabinets would otherwise get the whole stock and together promise
    WB twice what exists."""
    database, first, second = cabinets
    await load(database, line("A1", "2000000000017", 100))

    async with database.session() as session:
        await mapping(session).rematch(first)
    async with database.session() as session:
        await mapping(session).rematch(second)

    async with database.session() as session:
        state = await mapping(session).state()

    assert state.shared_without_rule == 1
    [shared] = state.shared
    assert sorted(shared.sellers, key=str) == sorted([first, second], key=str)
    assert shared.rule_ready is False


async def test_a_split_rule_makes_a_shared_pool_usable(cabinets) -> None:
    database, first, second = cabinets
    await load(database, line("A1", "2000000000017", 100))
    for seller_id in (first, second):
        async with database.session() as session:
            await mapping(session).rematch(seller_id)

    async with database.session() as session:
        state = await mapping(session).set_shares("A1", "", {first: 6000, second: 4000})

    assert state.shared_without_rule == 0
    assert state.shared[0].shares == {first: 6000, second: 4000}


async def test_a_split_that_is_not_a_whole_hundred_is_refused(cabinets) -> None:
    database, first, second = cabinets
    await load(database, line("A1", "2000000000017", 100))
    for seller_id in (first, second):
        async with database.session() as session:
            await mapping(session).rematch(seller_id)

    async with database.session() as session:
        with pytest.raises(InvalidShareError):
            await mapping(session).set_shares("A1", "", {first: 6000, second: 3000})


async def test_a_rule_that_misses_a_cabinet_does_not_count_as_ready(cabinets) -> None:
    """Covering only one of the two cabinets would leave the other publishing
    from a share nobody agreed."""
    database, first, second = cabinets
    await load(database, line("A1", "2000000000017", 100))
    for seller_id in (first, second):
        async with database.session() as session:
            await mapping(session).rematch(seller_id)

    async with database.session() as session:
        state = await mapping(session).set_shares("A1", "", {first: BASIS_POINTS})

    assert state.shared[0].rule_ready is False


async def test_a_pool_in_one_cabinet_needs_no_split_rule(cabinets) -> None:
    database, first, _ = cabinets
    await load(database, line("A2", "2000000000024"))

    async with database.session() as session:
        await mapping(session).rematch(first)
    async with database.session() as session:
        state = await mapping(session).state()

    assert state.shared == []
    assert state.mapped_pools == 1
