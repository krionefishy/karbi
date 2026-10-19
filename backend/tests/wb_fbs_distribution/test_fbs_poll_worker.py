from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_fbs_distribution.application import StockSnapshotSource
from backend.modules.wb_fbs_distribution.domain import StockLine, StockSnapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    StockPoolModel,
    StockSnapshotModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway
from backend.workers.wb_fbs_distribution.worker import FbsDistributionWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)


def line(quantity: int) -> StockLine:
    return StockLine(item_id="A1", barcode="2000", name="Товар", characteristic="", quantity=quantity)


def snapshot(quantity: int, generated_at: datetime = NOW) -> StockSnapshot:
    return StockSnapshot(generated_at=generated_at, lines=(line(quantity),))


class OneShotSource(StockSnapshotSource):
    """Источник, отдающий один и тот же снимок каждый опрос — как повёл бы
    себя простейший адаптер 1С без собственной отметки «уже забирали»."""

    source_id = "1c"

    def __init__(self, current: StockSnapshot | None) -> None:
        self.current = current
        self.calls = 0

    async def fetch(self) -> StockSnapshot | None:
        self.calls += 1
        return self.current


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


def polling_worker(database: Database, source: StockSnapshotSource) -> FbsDistributionWorker:
    return FbsDistributionWorker(database, WBFbsMarketplaceClient(make_gateway()), source, SETTINGS)


async def test_the_stub_keeps_the_poll_step_silent(database) -> None:
    worker = polling_worker(database, OneShotSource(None))

    assert await worker.poll_stock_source() is False

    async with database.session() as session:
        assert await FbsDistributionRepository(session).snapshot_history() == []


async def test_a_pulled_snapshot_is_accepted_once_not_every_tick(database) -> None:
    """Опрос идёт каждые полминуты; без отсечки по времени формирования журнал
    наполнялся бы повторами одного и того же снимка."""
    source = OneShotSource(snapshot(10))
    worker = polling_worker(database, source)

    first = await worker.poll_stock_source()
    second = await worker.poll_stock_source()

    assert (first, second) == (True, False)
    async with database.session() as session:
        history = await FbsDistributionRepository(session).snapshot_history()
    assert [record.status for record in history] == ["accepted"]
    assert source.calls == 2


async def test_a_fresher_snapshot_replaces_the_previous_one(database) -> None:
    source = OneShotSource(snapshot(10))
    worker = polling_worker(database, source)
    await worker.poll_stock_source()

    source.current = snapshot(4, generated_at=NOW + timedelta(minutes=15))

    assert await worker.poll_stock_source() is True
    async with database.session() as session:
        [pool] = await FbsDistributionRepository(session).pools()
    assert pool.quantity == 4


async def test_a_rejected_pull_is_logged_not_fatal(database) -> None:
    source = OneShotSource(snapshot(-5))
    worker = polling_worker(database, source)

    assert await worker.poll_stock_source() is False

    async with database.session() as session:
        [record] = await FbsDistributionRepository(session).snapshot_history()
    assert record.status == "rejected"
