import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta

import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy import update as sa_update

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import (
    ArticleModel,
    OutboxEventModel,
    SellerModel,
)
from backend.modules.wb_core.infrastructure.wb import WBTemporaryError
from backend.modules.wb_turnover.application import CalculationService, CollectionService, DigestService
from backend.modules.wb_turnover.domain import STATUS_NO_STOCK, STATUS_OK
from backend.modules.wb_turnover.infrastructure.postgres import (
    CollectionRunModel,
    OrderModel,
    RefreshRequestModel,
    StockSnapshotModel,
    TrackedSellerModel,
    TurnoverRepository,
)
from backend.modules.wb_turnover.infrastructure.postgres.repository import RECLAIM_RUNNING_AFTER
from backend.modules.wb_turnover.infrastructure.wb import (
    FBOStockRow,
    OrderRow,
    Warehouse,
    WBAnalyticsClient,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
TODAY = date(2026, 8, 20)
WINDOW = 14


class FakeStatistics(WBStatisticsClient):
    def __init__(self, order_rows=()) -> None:
        super().__init__(make_gateway())
        self.order_rows = list(order_rows)
        self.requested_from: datetime | None = None

    async def orders(self, seller_id: str, date_from: datetime) -> list[OrderRow]:
        self.requested_from = date_from
        return list(self.order_rows)


class FakeAnalytics(WBAnalyticsClient):
    def __init__(self, stock_rows=(), error: Exception | None = None) -> None:
        super().__init__(make_gateway())
        self.stock_rows = list(stock_rows)
        self.error = error

    async def stocks(self, seller_id: str) -> list[FBOStockRow]:
        if self.error is not None:
            raise self.error
        return list(self.stock_rows)


class FakeMarketplace(WBMarketplaceClient):
    """Amounts are keyed by (warehouse_id, chrt_id), so several warehouses can
    hold the same size."""

    def __init__(self, warehouse_list=(), amounts=None, error: Exception | None = None) -> None:
        super().__init__(make_gateway())
        self.warehouse_list = list(warehouse_list)
        self.amounts = amounts or {}
        self.error = error
        self.asked: list[tuple[int, tuple[int, ...]]] = []

    async def warehouses(self, seller_id: str) -> list[Warehouse]:
        if self.error is not None:
            raise self.error
        return list(self.warehouse_list)

    async def stocks(self, seller_id: str, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        if self.error is not None:
            raise self.error
        self.asked.append((warehouse_id, tuple(chrt_ids)))
        return {
            chrt_id: amount
            for (warehouse, chrt_id), amount in self.amounts.items()
            if warehouse == warehouse_id and chrt_id in chrt_ids
        }


def stock(article: str, quantity: int, chrt_id: int = 0, in_way: int = 0) -> FBOStockRow:
    return FBOStockRow(
        article=article,
        chrt_id=chrt_id or int(article),
        warehouse_id=-999999,
        warehouse_name="Склад WB",
        region_name="Склад WB",
        quantity=quantity,
        in_way_to_client=in_way,
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
        for article, chrt_ids in (("101", (1011, 1012)), ("102", (1021,))):
            session.add(
                ArticleModel(
                    seller_id=model.id,
                    article=article,
                    vendor_code=f"SKU-{article}",
                    name=f"Товар {article}",
                    sizes=[
                        {"chrt_id": chrt_id, "tech_size": str(index), "skus": [f"bar-{chrt_id}"]}
                        for index, chrt_id in enumerate(chrt_ids)
                    ],
                    state="active",
                )
            )
        session.add(TrackedSellerModel(seller_id=model.id))
        await session.commit()
    try:
        yield database, model.id
    finally:
        async with database.session() as session:
            await session.execute(delete(RefreshRequestModel).where(RefreshRequestModel.seller_id == model.id))
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
        bot = await BotRegistry(session, NotificationRepository(session)).register(
            code=code,
            title="Оборачиваемость",
            invite_link_template="https://t.me/test_bot?start={token}",
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
        BotRegistry(session, NotificationRepository(session)),
        threshold_days=10,
        bot_code=bot_code,
    )


def collection(session, analytics, marketplace, statistics=None) -> CollectionService:
    return CollectionService(
        session,
        SellerRepository(session),
        TurnoverRepository(session),
        statistics or FakeStatistics(),
        analytics,
        marketplace,
    )


async def collect_stocks(database, seller_id, analytics, marketplace, slot=0, day=TODAY):
    async with database.session() as session:
        return await collection(session, analytics, marketplace).collect_stocks(seller_id, day, slot)


async def collect_orders(database, seller_id, statistics, now=None) -> None:
    async with database.session() as session:
        await collection(session, FakeAnalytics(), FakeMarketplace(), statistics).collect_orders(
            seller_id,
            now or datetime(2026, 8, 20, 3, 20, tzinfo=UTC),
            backfill_days=WINDOW,
            overlap_hours=24,
        )


async def calculate(database, seller_id, day=TODAY):
    async with database.session() as session:
        return await CalculationService(
            session, SellerRepository(session), TurnoverRepository(session), window_days=WINDOW
        ).calculate(seller_id, day)


async def test_stock_of_an_article_wb_stopped_listing_is_written_as_zero(seller) -> None:
    """WB drops a sold-out article from the response; without a zero row the
    last non-zero snapshot would pass for the current stock forever."""
    database, seller_id = seller
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 40), stock("102", 5)]), FakeMarketplace())

    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 30)]), FakeMarketplace(), slot=1)

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
        FakeAnalytics([stock("101", 12)]),
        FakeMarketplace([Warehouse(1, "Свой склад")], {(1, 1011): 30}),
    )

    async with database.session() as session:
        rows = await session.scalars(select(StockSnapshotModel).where(StockSnapshotModel.seller_id == seller_id))
        by_model = {(row.article, row.delivery_model): row.quantity for row in rows}
    assert by_model[("101", "fbo")] == 12
    assert by_model[("101", "fbs")] == 30


async def test_the_same_slot_collected_twice_stays_one_point(seller) -> None:
    database, seller_id = seller

    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 40)]), FakeMarketplace())
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 10)]), FakeMarketplace())

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
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 42), stock("102", 0)]), FakeMarketplace())
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
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 4)]), FakeMarketplace())
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

    # Only 101: it is about to run out. 102 sits at zero as well, but nobody
    # ordered it in two weeks — that is the assortment tail, not an alert.
    assert (first.sent, first.articles) == (True, 1)
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
    assert set(items) == {"101"}
    assert items["101"]["out_of_stock"] is False


async def test_a_seller_with_nothing_low_gets_no_message(seller, digest_bot) -> None:
    database, seller_id = seller
    # Both articles well stocked: nothing low, and nothing at zero either.
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 400), stock("102", 400)]), FakeMarketplace())
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
        FakeAnalytics([stock("101", 10)]),
        FakeMarketplace(warehouse_list=[Warehouse(id=1, name="Свой")], amounts={(1, 1011): 7}),
        slot=0,
        day=yesterday,
    )
    # Today only FBO is collected — the seller dropped his own warehouse.
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 10)]), FakeMarketplace(), slot=0)

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
            super().__init__(make_gateway())
            self.cursors: list[datetime] = []

        async def request(self, method, path, seller_id, *, params=None, json=None):  # type: ignore[override]
            self.cursors.append(params["dateFrom"])
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


async def test_fbs_is_summed_over_every_size_and_every_warehouse(seller) -> None:
    """One товар lives under several chrtId at several addresses."""
    database, seller_id = seller

    await collect_stocks(
        database,
        seller_id,
        FakeAnalytics([stock("101", 0)]),
        FakeMarketplace(
            [Warehouse(1, "Москва"), Warehouse(2, "Казань")],
            {(1, 1011): 10, (1, 1012): 5, (2, 1011): 3},
        ),
    )

    async with database.session() as session:
        current = await TurnoverRepository(session).latest_stock(seller_id, TODAY - timedelta(days=1))
    assert current["101"].fbs == 18


async def test_a_marketplace_outage_keeps_the_wb_stock_and_the_previous_fbs(seller) -> None:
    """An FBS failure must not discard a good FBO reading, and must not write
    zeros that would read as «товар кончился»."""
    database, seller_id = seller
    await collect_stocks(
        database,
        seller_id,
        FakeAnalytics([stock("101", 10)]),
        FakeMarketplace([Warehouse(1, "Москва")], {(1, 1011): 7}),
        slot=0,
    )

    result = await collect_stocks(
        database,
        seller_id,
        FakeAnalytics([stock("101", 12)]),
        FakeMarketplace(error=WBTemporaryError("Bad Gateway")),
        slot=1,
    )

    assert result.fbs_failed is True
    async with database.session() as session:
        rows = await session.scalars(
            select(StockSnapshotModel).where(StockSnapshotModel.seller_id == seller_id, StockSnapshotModel.slot == 1)
        )
        by_model = {row.delivery_model: row.quantity for row in rows if row.article == "101"}
    # The new slot holds the fresh FBO figure and no FBS row at all.
    assert by_model == {"fbo": 12}


async def test_a_seller_without_own_warehouses_is_not_a_failure(seller) -> None:
    database, seller_id = seller

    result = await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 10)]), FakeMarketplace())

    assert (result.fbs, result.fbs_failed) == ("absent", False)
    assert result.articles == 2


async def test_goods_in_transit_stay_out_of_the_stock_that_is_counted(seller) -> None:
    """inWayToClient is not on the shelf: counting it would delay the alarm."""
    database, seller_id = seller

    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 3, in_way=400)]), FakeMarketplace())

    async with database.session() as session:
        current = await TurnoverRepository(session).latest_stock(seller_id, TODAY - timedelta(days=1))
        row = await session.scalar(
            select(StockSnapshotModel).where(
                StockSnapshotModel.seller_id == seller_id,
                StockSnapshotModel.article == "101",
                StockSnapshotModel.delivery_model == "fbo",
            )
        )
    assert current["101"].total == 3
    assert row is not None and row.in_way_to_client == 400
    # quantity_full is derived now that WB stopped sending it.
    assert row.quantity_full == 403


async def test_an_empty_shelf_is_reported_only_for_a_товар_that_was_selling(seller, digest_bot) -> None:
    """«Кончился то, что продавалось» is the alert. «Нет остатка и не заказывали»
    is the assortment tail — for one live seller it was 927 rows against 28."""
    database, seller_id = seller
    # Both articles are at zero; only 102 has been selling.
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 0), stock("102", 0)]), FakeMarketplace())
    await collect_orders(
        database,
        seller_id,
        FakeStatistics(order_rows=[order(f"o{index}", "102", date(2026, 8, 19)) for index in range(5)]),
    )
    await calculate(database, seller_id)

    async with database.session() as session:
        result = await digest_service(session, digest_bot).send(seller_id, TODAY)

    assert (result.sent, result.articles) == (True, 1)
    async with database.session() as session:
        event = await session.scalar(
            select(OutboxEventModel).where(
                OutboxEventModel.aggregate_id == seller_id,
                OutboxEventModel.event_type == "TurnoverDigestRequested",
            )
        )
    assert event is not None
    assert [item["article"] for item in event.payload["params"]["items"]] == ["102"]


async def test_cards_wb_no_longer_lists_are_left_out_of_the_metric(seller) -> None:
    """A feedback_only or archived card cannot be restocked, so its turnover is
    meaningless — and it is where most of the zero rows came from."""
    database, seller_id = seller
    async with database.session() as session:
        await session.execute(
            update(ArticleModel)
            .where(ArticleModel.seller_id == seller_id, ArticleModel.article == "102")
            .values(state="feedback_only")
        )
        await session.commit()

    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 5), stock("102", 7)]), FakeMarketplace())
    rows = await calculate(database, seller_id)

    assert [row.article for row in rows] == ["101"]


async def test_pressing_refresh_twice_asks_wildberries_once(seller) -> None:
    """The button is a wish, not a call: two presses must not double the walk
    over a rate-limited API."""
    database, seller_id = seller

    async with database.session() as session:
        repository = TurnoverRepository(session)
        first = await repository.request_refresh(seller_id, None)
        second = await repository.request_refresh(seller_id, None)
        await session.commit()

    assert first.id == second.id
    async with database.session() as session:
        queued = await TurnoverRepository(session).claim_refreshes()
        await session.commit()
    assert [request.seller_id for request in queued] == [seller_id]


async def test_a_claimed_request_is_not_handed_out_again(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await TurnoverRepository(session).request_refresh(seller_id, None)
        await session.commit()

    async with database.session() as session:
        await TurnoverRepository(session).claim_refreshes()
        await session.commit()
    async with database.session() as session:
        second_round = await TurnoverRepository(session).claim_refreshes()
        await session.commit()

    assert second_round == []


async def test_a_refresh_that_failed_says_so_instead_of_hanging(seller) -> None:
    """A request left «running» would block the button for that seller forever."""
    database, seller_id = seller
    async with database.session() as session:
        repository = TurnoverRepository(session)
        request = await repository.request_refresh(seller_id, None)
        request_id = request.id
        await session.commit()
    async with database.session() as session:
        await TurnoverRepository(session).claim_refreshes()
        await session.commit()

    async with database.session() as session:
        await TurnoverRepository(session).finish_refresh(request_id, "WB Analytics API: ключ не имеет доступа")
        await session.commit()

    async with database.session() as session:
        repository = TurnoverRepository(session)
        latest = await repository.latest_refresh(seller_id)
        assert latest is not None
        assert latest.status == "error"
        assert latest.finished_at is not None
        # And the seller can ask again.
        assert await repository.active_refresh(seller_id) is None


async def test_a_recalculation_takes_away_what_it_no_longer_counts(seller) -> None:
    """A товар that left the catalog must not keep its last computed row."""
    database, seller_id = seller
    await collect_stocks(database, seller_id, FakeAnalytics([stock("101", 5), stock("102", 5)]), FakeMarketplace())
    assert {row.article for row in await calculate(database, seller_id)} == {"101", "102"}

    async with database.session() as session:
        await session.execute(
            update(ArticleModel)
            .where(ArticleModel.seller_id == seller_id, ArticleModel.article == "102")
            .values(state="archived")
        )
        await session.commit()
    await calculate(database, seller_id)

    async with database.session() as session:
        left = await TurnoverRepository(session).turnover_on(seller_id, TODAY)
    assert [row.article for row in left] == ["101"]
