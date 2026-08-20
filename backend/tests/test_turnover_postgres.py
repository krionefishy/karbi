import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy import update as sa_update

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import (
    ArticleModel,
    CredentialModel,
    OutboxEventModel,
    SellerModel,
)
from backend.modules.wb_turnover.application import CalculationService, CollectionService, DigestService
from backend.modules.wb_turnover.domain import STATUS_NO_STOCK, STATUS_OK
from backend.modules.wb_turnover.infrastructure.postgres import (
    CollectionRunModel,
    OrderModel,
    StockSnapshotModel,
    TrackedSellerModel,
    TurnoverRepository,
)
from backend.modules.wb_turnover.infrastructure.postgres.repository import RECLAIM_RUNNING_AFTER
from backend.modules.wb_turnover.infrastructure.wb import (
    OrderRow,
    StockRow,
    Warehouse,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
TODAY = date(2026, 8, 20)
WINDOW = 14


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


class FakeStatistics(WBStatisticsClient):
    def __init__(self, stock_rows=(), order_rows=()) -> None:
        super().__init__()
        self.stock_rows = list(stock_rows)
        self.order_rows = list(order_rows)
        self.requested_from: datetime | None = None

    async def stocks(self, api_key: str) -> list[StockRow]:
        return list(self.stock_rows)

    async def orders(self, api_key: str, date_from: datetime) -> list[OrderRow]:
        self.requested_from = date_from
        return list(self.order_rows)


class FakeMarketplace(WBMarketplaceClient):
    def __init__(self, warehouse_list=(), amounts=None) -> None:
        super().__init__()
        self.warehouse_list = list(warehouse_list)
        self.amounts = amounts or {}

    async def warehouses(self, api_key: str) -> list[Warehouse]:
        return list(self.warehouse_list)

    async def stocks(self, api_key: str, warehouse_id: int, skus: list[str]) -> dict[str, int]:
        return {sku: amount for sku, amount in self.amounts.items() if sku in skus}


def stock(article: str, quantity: int, warehouse: str = "Коледино") -> StockRow:
    return StockRow(
        article=article,
        barcode=f"bar-{article}",
        warehouse_name=warehouse,
        quantity=quantity,
        quantity_full=quantity,
        in_way_to_client=0,
        in_way_from_client=0,
    )


def order(srid: str, article: str, day: date, *, cancelled: bool = False, changed: datetime | None = None) -> OrderRow:
    return OrderRow(
        srid=srid,
        article=article,
        order_date=day,
        last_change_date=changed or datetime.combine(day, datetime.min.time()),
        is_cancel=cancelled,
        price=1000.0,
        warehouse_type="Склад WB",
    )


@pytest_asyncio.fixture
async def seller() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    model = SellerModel(name="Оборачиваемость тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(model)
        await session.flush()
        session.add(
            CredentialModel(
                seller_id=model.id,
                encrypted_api_key=cipher().encrypt("wb-turnover-key"),
                key_fingerprint=uuid.uuid4().hex,
            )
        )
        for article, size_skus in (("101", ["bar-101"]), ("102", ["bar-102"])):
            session.add(
                ArticleModel(
                    seller_id=model.id,
                    article=article,
                    vendor_code=f"SKU-{article}",
                    name=f"Товар {article}",
                    sizes=[{"chrt_id": 1, "tech_size": "0", "skus": size_skus}],
                    state="active",
                )
            )
        session.add(TrackedSellerModel(seller_id=model.id))
        await session.commit()
    try:
        yield database, model.id
    finally:
        async with database.session() as session:
            await TurnoverRepository(session).purge_seller(model.id)
            # Runs are not per seller, and their slot key is unique, so leaving
            # one behind would make the next test run see the slot as taken.
            await session.execute(delete(CollectionRunModel))
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == model.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == model.id))
            await session.commit()
        await database.disconnect()


@pytest_asyncio.fixture
async def digest_bot(seller) -> AsyncIterator[str]:
    """A registered bot, because the digest refuses to burn a day without one."""
    database, _ = seller
    code = f"turnover-alerts-{uuid.uuid4().hex[:8]}"
    async with database.session() as session:
        bot = await BotRegistry(session, NotificationRepository(session), cipher()).register(
            code=code,
            username="karbi_turnover_bot",
            title="Оборачиваемость",
            token=f"1234:{uuid.uuid4().hex}",
        )
    try:
        yield code
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id == bot.id))
            await session.commit()


def digest_service(session, bot_code: str) -> DigestService:
    return DigestService(
        session,
        SellerRepository(session),
        TurnoverRepository(session),
        BotRegistry(session, NotificationRepository(session), cipher()),
        threshold_days=10,
        bot_code=bot_code,
    )


def collection(session, statistics, marketplace) -> CollectionService:
    return CollectionService(
        session, SellerRepository(session), TurnoverRepository(session), cipher(), statistics, marketplace
    )


async def collect_stocks(database, seller_id, statistics, marketplace, slot=0, day=TODAY) -> None:
    async with database.session() as session:
        await collection(session, statistics, marketplace).collect_stocks(seller_id, day, slot)


async def collect_orders(database, seller_id, statistics, now=None) -> None:
    async with database.session() as session:
        await collection(session, statistics, FakeMarketplace()).collect_orders(
            seller_id,
            now or datetime(2026, 8, 20, 3, 20, tzinfo=UTC),
            backfill_days=WINDOW,
            overlap_hours=24,
        )


async def calculate(database, seller_id, day=TODAY):
    async with database.session() as session:
        return await CalculationService(session, TurnoverRepository(session), window_days=WINDOW).calculate(
            seller_id, day
        )


async def test_stock_of_an_article_wb_stopped_listing_is_written_as_zero(seller) -> None:
    """WB drops a sold-out article from the response; without a zero row the
    last non-zero snapshot would pass for the current stock forever."""
    database, seller_id = seller
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 40), stock("102", 5)]), FakeMarketplace())

    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 30)]), FakeMarketplace(), slot=1)

    async with database.session() as session:
        rows = await session.scalars(
            select(StockSnapshotModel).where(StockSnapshotModel.seller_id == seller_id, StockSnapshotModel.slot == 1)
        )
        quantities = {row.article: row.quantity for row in rows}
    assert quantities == {"101": 30, "102": 0}


async def test_stock_from_both_delivery_models_lands_in_one_snapshot(seller) -> None:
    database, seller_id = seller

    await collect_stocks(
        database,
        seller_id,
        FakeStatistics([stock("101", 12)]),
        FakeMarketplace([Warehouse(1, "Свой склад")], {"bar-101": 30}),
    )

    async with database.session() as session:
        rows = await session.scalars(select(StockSnapshotModel).where(StockSnapshotModel.seller_id == seller_id))
        by_model = {(row.article, row.delivery_model): row.quantity for row in rows}
    assert by_model[("101", "fbo")] == 12
    assert by_model[("101", "fbs")] == 30


async def test_the_same_slot_collected_twice_stays_one_point(seller) -> None:
    database, seller_id = seller

    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 40)]), FakeMarketplace())
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 10)]), FakeMarketplace())

    async with database.session() as session:
        rows = list(
            await session.scalars(
                select(StockSnapshotModel).where(
                    StockSnapshotModel.seller_id == seller_id,
                    StockSnapshotModel.delivery_model == "fbo",
                    StockSnapshotModel.article == "101",
                )
            )
        )
    assert len(rows) == 1 and rows[0].quantity == 10


async def test_a_cancelled_order_stops_counting_when_it_comes_back(seller) -> None:
    """Counters could only grow; keyed by srid the same order is simply rewritten."""
    database, seller_id = seller
    await collect_orders(database, seller_id, FakeStatistics(order_rows=[order("a", "101", date(2026, 8, 18))]))

    await collect_orders(
        database,
        seller_id,
        FakeStatistics(
            order_rows=[
                order("a", "101", date(2026, 8, 18), cancelled=True, changed=datetime(2026, 8, 19, 12)),
            ]
        ),
    )

    async with database.session() as session:
        rows = list(await session.scalars(select(OrderModel).where(OrderModel.seller_id == seller_id)))
        window = await TurnoverRepository(session).orders_in_window(
            seller_id, TODAY - timedelta(days=WINDOW), TODAY - timedelta(days=1)
        )
    assert len(rows) == 1 and rows[0].is_cancel is True
    assert window["101"].orders == 0 and window["101"].cancelled == 1


async def test_the_orders_watermark_moves_forward_and_is_reused(seller) -> None:
    database, seller_id = seller
    changed = datetime(2026, 8, 19, 15, 30)
    await collect_orders(
        database, seller_id, FakeStatistics(order_rows=[order("a", "101", date(2026, 8, 19), changed=changed)])
    )

    second = FakeStatistics(order_rows=[])
    await collect_orders(database, seller_id, second)

    # The next pull starts a day before the watermark, not from the backfill window.
    assert second.requested_from is not None
    assert second.requested_from.date() == date(2026, 8, 18)


async def test_a_failed_pull_does_not_move_the_watermark(seller) -> None:
    database, seller_id = seller

    empty = FakeStatistics(order_rows=[])
    await collect_orders(database, seller_id, empty)

    async with database.session() as session:
        tracked = await TurnoverRepository(session).tracked(seller_id)
    assert tracked is not None and tracked.orders_watermark is None


async def test_the_metric_is_computed_from_what_was_collected(seller) -> None:
    database, seller_id = seller
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 42), stock("102", 0)]), FakeMarketplace())
    await collect_orders(
        database,
        seller_id,
        FakeStatistics(order_rows=[order(f"o{index}", "101", date(2026, 8, 19)) for index in range(28)]),
    )

    rows = {row.article: row for row in await calculate(database, seller_id)}

    assert rows["101"].status == STATUS_OK
    assert rows["101"].days_of_cover == 42 / 28  # 28 orders on one day of sale
    assert rows["102"].status == STATUS_NO_STOCK


async def test_the_digest_goes_out_once_a_day_and_only_when_something_is_low(seller, digest_bot) -> None:
    database, seller_id = seller
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 4)]), FakeMarketplace())
    await collect_orders(
        database,
        seller_id,
        FakeStatistics(order_rows=[order(f"o{index}", "101", date(2026, 8, 19)) for index in range(28)]),
    )
    await calculate(database, seller_id)

    async def send():
        async with database.session() as session:
            return await digest_service(session, digest_bot).send(seller_id, TODAY)

    first, second = await send(), await send()

    # 101 is low on cover, 102 sits at zero — the empty shelf belongs in the
    # digest just as much as the one about to empty.
    assert (first.sent, first.articles) == (True, 2)
    assert second.sent is False
    async with database.session() as session:
        events = list(
            await session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.aggregate_id == seller_id,
                    OutboxEventModel.event_type == "TurnoverDigestRequested",
                )
            )
        )
    assert len(events) == 1
    payload = events[0].payload
    assert payload["bot"] == digest_bot
    assert payload["audience"] == {"type": "seller_subscribers", "seller_id": str(seller_id)}
    items = {item["article"]: item for item in payload["params"]["items"]}
    assert set(items) == {"101", "102"}
    assert items["101"]["out_of_stock"] is False
    assert items["102"]["out_of_stock"] is True


async def test_a_seller_with_nothing_low_gets_no_message(seller, digest_bot) -> None:
    database, seller_id = seller
    # Both articles well stocked: nothing low, and nothing at zero either.
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 400), stock("102", 400)]), FakeMarketplace())
    await collect_orders(
        database,
        seller_id,
        FakeStatistics(order_rows=[order("o1", "101", date(2026, 8, 19))]),
    )
    await calculate(database, seller_id)

    async with database.session() as session:
        result = await digest_service(session, digest_bot).send(seller_id, TODAY)

    assert (result.sent, result.articles) == (False, 0)


async def test_a_schedule_slot_can_only_be_claimed_once(seller) -> None:
    database, _ = seller

    async with database.session() as session:
        repository = TurnoverRepository(session)
        first = await repository.start_run("stocks", TODAY, 0)
        await session.commit()
    async with database.session() as session:
        repeated = await TurnoverRepository(session).start_run("stocks", TODAY, 0)
        await session.rollback()

    assert first is not None and repeated is None


async def test_a_crashed_slot_can_be_claimed_again_once_it_goes_stale(seller) -> None:
    database, _ = seller

    async with database.session() as session:
        claimed = await TurnoverRepository(session).start_run("stocks", TODAY, 1)
        await session.commit()
    assert claimed is not None
    # Pretend the process died right after claiming: the run is still "running"
    # and older than the reclaim window.
    async with database.session() as session:
        await session.execute(
            sa_update(CollectionRunModel)
            .where(CollectionRunModel.id == claimed.id)
            .values(started_at=datetime.now(UTC) - RECLAIM_RUNNING_AFTER - timedelta(minutes=1))
        )
        await session.commit()

    async with database.session() as session:
        again = await TurnoverRepository(session).start_run("stocks", TODAY, 1)
        await session.commit()

    assert again is not None


async def test_a_finished_slot_stays_claimed(seller) -> None:
    database, _ = seller

    async with database.session() as session:
        repository = TurnoverRepository(session)
        run = await repository.start_run("stocks", TODAY, 2)
        assert run is not None
        await repository.finish_run(run.id, sellers=1, failed=0)
        await session.execute(
            sa_update(CollectionRunModel)
            .where(CollectionRunModel.id == run.id)
            .values(started_at=datetime.now(UTC) - RECLAIM_RUNNING_AFTER - timedelta(hours=2))
        )
        await session.commit()

    async with database.session() as session:
        repeated = await TurnoverRepository(session).start_run("stocks", TODAY, 2)
        await session.rollback()

    assert repeated is None


async def test_the_current_stock_never_mixes_two_collection_points(seller) -> None:
    """FBO from this morning must not be added to yesterday's FBS figure."""
    database, seller_id = seller
    yesterday = TODAY - timedelta(days=1)
    await collect_stocks(
        database,
        seller_id,
        FakeStatistics([stock("101", 10)]),
        FakeMarketplace(warehouse_list=[Warehouse(id=1, name="Свой")], amounts={"bar-101": 7}),
        slot=0,
        day=yesterday,
    )
    # Today only FBO is collected — the seller dropped his own warehouse.
    await collect_stocks(database, seller_id, FakeStatistics([stock("101", 10)]), FakeMarketplace(), slot=0)

    async with database.session() as session:
        current = await TurnoverRepository(session).latest_stock(seller_id, TODAY - timedelta(days=7))

    assert current["101"].total == 10  # not 17


async def test_orders_are_read_page_by_page_until_nothing_new_arrives() -> None:
    pages = [
        [order(f"a{index}", "101", date(2026, 8, 18), changed=datetime(2026, 8, 18, index)) for index in range(3)],
        [order(f"b{index}", "101", date(2026, 8, 19), changed=datetime(2026, 8, 19, index)) for index in range(3)],
        [],
    ]

    class Paged(WBStatisticsClient):
        def __init__(self) -> None:
            super().__init__()
            self.cursors: list[datetime] = []

        async def request(self, client, method, url, api_key, **kwargs):  # type: ignore[override]
            self.cursors.append(kwargs["params"]["dateFrom"])
            return [
                {
                    "srid": row.srid,
                    "nmId": int(row.article),
                    "date": row.order_date.isoformat(),
                    "lastChangeDate": row.last_change_date.isoformat(),
                    "isCancel": row.is_cancel,
                    "finishedPrice": row.price,
                    "warehouseType": row.warehouse_type,
                }
                for row in pages[min(len(self.cursors) - 1, len(pages) - 1)]
            ]

    client = Paged()
    rows = await client.orders("key", datetime(2026, 8, 18, tzinfo=UTC))

    assert {row.srid for row in rows} == {"a0", "a1", "a2", "b0", "b1", "b2"}
    assert len(client.cursors) == 3  # two pages of data, one empty page to stop


async def test_a_repeated_srid_inside_one_pull_does_not_break_the_batch(seller) -> None:
    database, seller_id = seller
    # The same order twice in one response: pages overlap on their boundary row.
    await collect_orders(
        database,
        seller_id,
        FakeStatistics(
            order_rows=[
                order("dup", "101", date(2026, 8, 19)),
                order("dup", "101", date(2026, 8, 19), cancelled=True),
            ]
        ),
    )

    async with database.session() as session:
        rows = list(await session.scalars(select(OrderModel).where(OrderModel.seller_id == seller_id)))

    assert len(rows) == 1
    assert rows[0].is_cancel is True  # the last row wins


async def test_a_purge_mid_flight_is_not_overwritten_by_the_write_phase(seller) -> None:
    database, seller_id = seller
    # The seller leaves the automation while the pull is in the air.
    async with database.session() as session:
        await session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))
        await session.commit()

    await collect_orders(database, seller_id, FakeStatistics(order_rows=[order("o1", "101", date(2026, 8, 19))]))

    async with database.session() as session:
        rows = list(await session.scalars(select(OrderModel).where(OrderModel.seller_id == seller_id)))
    assert rows == []
