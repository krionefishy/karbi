import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_fbs_distribution.application import (
    ONEC,
    DisconnectedSource,
    SnapshotRejected,
    SnapshotService,
)
from backend.modules.wb_fbs_distribution.domain import StockLine, StockSnapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    StockPoolModel,
    StockSnapshotModel,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def line(item_id: str, barcode: str, quantity: int, characteristic: str = "") -> StockLine:
    return StockLine(
        item_id=item_id,
        barcode=barcode,
        name=f"Товар {item_id}",
        characteristic=characteristic,
        quantity=quantity,
    )


def snapshot(*lines: StockLine, generated_at: datetime | None = None) -> StockSnapshot:
    return StockSnapshot(generated_at=generated_at or NOW, lines=tuple(lines))


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    db = Database()
    await db.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield db
    finally:
        async with db.session() as session:
            await session.execute(delete(StockPoolModel))
            await session.execute(delete(StockSnapshotModel))
            await session.commit()
        await db.disconnect()


def service(session) -> SnapshotService:
    return SnapshotService(
        session,
        FbsDistributionRepository(session),
        max_age_minutes=SETTINGS.fbs_distribution.snapshot_max_age_minutes,
    )


async def test_the_stub_source_admits_it_has_nothing(database) -> None:
    """Until the exchange exists the automation must say so, not invent stock."""
    assert await DisconnectedSource().fetch() is None


async def test_an_accepted_snapshot_becomes_pools(database) -> None:
    async with database.session() as session:
        state = await service(session).accept(
            snapshot(line("A1", "2000", 150), line("A1", "2001", 8, "L")), source=ONEC, now=NOW
        )

    assert (state.pools, state.on_hand_total) == (2, 158)
    # 150 - 20 и 8 без резерва: на малом остатке резерв не берётся.
    assert state.available_total == 138
    assert state.stale is False


async def test_a_line_gone_from_the_snapshot_is_zeroed_not_forgotten(database) -> None:
    """Deleting the row would drop the mapping to the WB card with it; leaving
    the old quantity would keep selling stock 1C no longer has."""
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 10), line("A2", "2001", 5)), source=ONEC, now=NOW)
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 7)), source=ONEC, now=NOW)

    async with database.session() as session:
        pools = {pool.item_id: pool.quantity for pool in await FbsDistributionRepository(session).pools()}
    assert pools == {"A1": 7, "A2": 0}


async def test_the_same_snapshot_twice_does_not_double_anything(database) -> None:
    """An absolute snapshot is what makes a repeated delivery safe."""
    twice = snapshot(line("A1", "2000", 10))
    async with database.session() as session:
        await service(session).accept(twice, source=ONEC, now=NOW)
    async with database.session() as session:
        state = await service(session).accept(twice, source=ONEC, now=NOW)

    assert (state.pools, state.on_hand_total) == (1, 10)


async def test_a_negative_quantity_is_refused_whole(database) -> None:
    """Partly accepting would mix fresh and yesterday's rows, and the published
    number would stop being explainable."""
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 10)), source=ONEC, now=NOW)

    async with database.session() as session:
        with pytest.raises(SnapshotRejected, match="Отрицательный"):
            await service(session).accept(snapshot(line("A1", "2000", 5), line("A2", "2001", -1)), source=ONEC, now=NOW)

    async with database.session() as session:
        pools = {pool.item_id: pool.quantity for pool in await FbsDistributionRepository(session).pools()}
    assert pools == {"A1": 10}


async def test_a_repeated_barcode_is_refused(database) -> None:
    """One barcode is one WB size; two lines on it would double the stock the
    moment they are mapped."""
    async with database.session() as session:
        with pytest.raises(SnapshotRejected, match="Баркод"):
            await service(session).accept(snapshot(line("A1", "2000", 5), line("A2", "2000", 3)), source=ONEC, now=NOW)


async def test_the_same_item_and_characteristic_twice_is_refused(database) -> None:
    async with database.session() as session:
        with pytest.raises(SnapshotRejected, match="дважды"):
            await service(session).accept(snapshot(line("A1", "2000", 5), line("A1", "2001", 3)), source=ONEC, now=NOW)


async def test_an_empty_snapshot_never_wipes_the_pools(database) -> None:
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 10)), source=ONEC, now=NOW)

    async with database.session() as session:
        with pytest.raises(SnapshotRejected):
            await service(session).accept(snapshot(), source=ONEC, now=NOW)

    async with database.session() as session:
        assert (await service(session).state(now=NOW)).on_hand_total == 10


async def test_a_snapshot_from_the_future_is_refused(database) -> None:
    async with database.session() as session:
        with pytest.raises(SnapshotRejected, match="будущем"):
            await service(session).accept(
                snapshot(line("A1", "2000", 5), generated_at=NOW + timedelta(hours=1)), source=ONEC, now=NOW
            )


async def test_a_refusal_is_written_down_too(database) -> None:
    """«Сегодня ничего не менялось» и «сегодня приехал битый файл» — разные
    события, и второе оператор обязан видеть."""
    async with database.session() as session:
        with pytest.raises(SnapshotRejected):
            await service(session).accept(snapshot(line("A1", "2000", -5)), source=ONEC, now=NOW)

    async with database.session() as session:
        [record] = await FbsDistributionRepository(session).snapshot_history()
    assert record.status == "rejected"
    assert record.error and "Отрицательный" in record.error


async def test_a_stale_snapshot_is_reported_as_stale(database) -> None:
    old = NOW - timedelta(minutes=SETTINGS.fbs_distribution.snapshot_max_age_minutes + 1)
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 10), generated_at=old), source=ONEC, now=NOW)

    async with database.session() as session:
        assert (await service(session).state(now=NOW)).stale is True


async def test_without_a_snapshot_the_state_says_disconnected(database) -> None:
    async with database.session() as session:
        state = await service(session).state(now=NOW)

    assert state.snapshot_id is None
    assert state.source == "disconnected"
    assert state.stale is True


async def test_only_accepted_snapshots_are_the_current_one(database) -> None:
    async with database.session() as session:
        await service(session).accept(snapshot(line("A1", "2000", 10)), source=ONEC, now=NOW)
    later = NOW + timedelta(minutes=1)
    async with database.session() as session:
        with pytest.raises(SnapshotRejected):
            await service(session).accept(snapshot(line("A1", "2000", -1)), source=ONEC, now=later)

    async with database.session() as session:
        state = await service(session).state(now=later)
    assert state.lines == 1
    assert state.generated_at == NOW
    assert isinstance(state.snapshot_id, uuid.UUID)


async def test_pools_can_be_searched_by_barcode_or_name(database) -> None:
    async with database.session() as session:
        await service(session).accept(
            snapshot(line("A1", "2000000000017", 10), line("A2", "2000000000024", 5)), source=ONEC, now=NOW
        )

    async with database.session() as session:
        found = await service(session).pools(search="0000017")
    assert [pool.item_id for pool in found] == ["A1"]
