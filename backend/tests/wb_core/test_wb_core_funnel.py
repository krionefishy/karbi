import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete

from backend.modules.wb_core.domain import MIRROR_FUNNEL
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import MirrorStateModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import WBAnalyticsClient, WBTemporaryError
from backend.modules.wb_core.infrastructure.wb.analytics import FUNNEL_PAGE_LIMIT, FUNNEL_PATH
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway
from backend.tests.wb_core.test_wb_core_mirror_postgres import SETTINGS, FakeAnalytics, mirror
from backend.workers.wb_core.worker import WBCoreWorker

MOSCOW = ZoneInfo("Europe/Moscow")
SELLER = "seller-1"


def product(nm_id: object, orders: object) -> dict:
    return {"product": {"nmId": nm_id}, "statistic": {"selected": {"orderCount": orders}}}


async def test_daily_orders_are_read_per_nm_id_for_one_day_and_junk_is_skipped() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            FUNNEL_PATH,
            body={
                "data": {
                    "products": [
                        product(1496386741, 122),
                        product(1001, 0),
                        product("bad", 5),
                        product(1002, None),
                        product(1003, True),
                        {"product": {"nmId": 1004}},
                    ]
                }
            },
        )
        orders = await WBAnalyticsClient(make_gateway()).daily_orders(SELLER, date(2026, 11, 4))

    assert orders == {"1496386741": 122}
    envelope = stub.requests_to(FUNNEL_PATH)[-1]
    assert envelope["body"] == {
        "selectedPeriod": {"start": "2026-11-04", "end": "2026-11-04"},
        "limit": FUNNEL_PAGE_LIMIT,
        "offset": 0,
    }


async def test_daily_orders_follow_pages() -> None:
    first = [product(index, 1) for index in range(1, FUNNEL_PAGE_LIMIT + 1)]
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            FUNNEL_PATH,
            side_effect=[(200, {"data": {"products": first}}), (200, {"data": {"products": [product(5000, 3)]}})],
        )
        orders = await WBAnalyticsClient(make_gateway()).daily_orders(SELLER, date(2026, 11, 4))

    assert len(orders) == FUNNEL_PAGE_LIMIT + 1
    assert [call["body"]["offset"] for call in stub.requests_to(FUNNEL_PATH)] == [0, FUNNEL_PAGE_LIMIT]


class FunnelAnalytics(FakeAnalytics):
    def __init__(self, by_day: dict[date, dict[str, int]], *, failing_on: date | None = None) -> None:
        super().__init__([])
        self.by_day = by_day
        self.failing_on = failing_on
        self.asked: list[date] = []

    async def daily_orders(self, seller_id: str, day: date) -> dict[str, int]:
        self.asked.append(day)
        if day == self.failing_on:
            raise WBTemporaryError("воронка недоступна")
        return dict(self.by_day.get(day, {}))


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield database
    finally:
        await database.disconnect()


@pytest_asyncio.fixture
async def seller(database: Database) -> AsyncIterator[uuid.UUID]:
    async with database.session() as session:
        model = SellerModel(name="ИП Воронка", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


def moscow(day: date, hour: int) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=MOSCOW).replace(hour=hour).astimezone(UTC)


async def test_every_past_day_of_the_window_is_read_and_only_fresh_days_are_reread(
    database: Database, seller: uuid.UUID
) -> None:
    today = date(2026, 11, 5)
    days = [today - timedelta(days=offset) for offset in range(14, 0, -1)]
    analytics = FunnelAnalytics({today - timedelta(days=1): {"1496386741": 122, "1001": 3}})
    service = mirror(database, analytics=analytics)

    outcome = await service.collect_funnel(seller, now=moscow(today, 10))

    assert analytics.asked == days
    assert (outcome.days, outcome.articles) == (14, 2)
    async with database.session() as session:
        covered, orders = await MirrorRepository(session).funnel_orders(seller, days[-7], days[-1])
    assert covered == set(days[-7:])
    assert orders == {"1496386741": 122, "1001": 3}

    analytics.asked.clear()
    await service.collect_funnel(seller, now=moscow(today, 11))
    # Вчерашний день прочитан меньше чем через сутки после конца и ещё может дописаться;
    # позавчерашний прочитан сегодня — уже устоялся.
    assert analytics.asked == [today - timedelta(days=1)]


async def test_a_reread_day_replaces_its_counts(database: Database, seller: uuid.UUID) -> None:
    today = date(2026, 11, 5)
    day = today - timedelta(days=1)
    await mirror(database, analytics=FunnelAnalytics({day: {"1001": 3, "1002": 1}})).collect_funnel(
        seller, now=moscow(today, 10)
    )
    await mirror(database, analytics=FunnelAnalytics({day: {"1001": 5}})).collect_funnel(seller, now=moscow(today, 12))

    async with database.session() as session:
        _, orders = await MirrorRepository(session).funnel_orders(seller, day, day)
    assert orders == {"1001": 5}


async def test_a_failure_keeps_the_days_already_read(database: Database, seller: uuid.UUID) -> None:
    today = date(2026, 11, 5)
    broken = today - timedelta(days=3)
    analytics = FunnelAnalytics({today - timedelta(days=5): {"1001": 2}}, failing_on=broken)

    with pytest.raises(WBTemporaryError):
        await mirror(database, analytics=analytics).collect_funnel(seller, now=moscow(today, 10))

    async with database.session() as session:
        covered, orders = await MirrorRepository(session).funnel_orders(seller, today - timedelta(days=7), today)
        state = await session.get(MirrorStateModel, (seller, MIRROR_FUNNEL))
    assert max(covered) == broken - timedelta(days=1)
    assert orders == {"1001": 2}
    assert state is not None and state.collected_at is None and "воронка" in (state.error or "")


async def test_old_funnel_days_are_pruned(database: Database, seller: uuid.UUID) -> None:
    today = date(2026, 11, 5)
    old = today - timedelta(days=SETTINGS.core_mirror.funnel_retention_days + 1)
    async with database.session() as session:
        repository = MirrorRepository(session)
        await repository.replace_funnel_day(seller, old, {"1001": 4}, now=moscow(today, 1))
        await repository.replace_funnel_day(seller, today - timedelta(days=1), {"1001": 1}, now=moscow(today, 1))
        await session.commit()

    await WBCoreWorker(database, mirror(database), SETTINGS).prune(moscow(today, 10))

    async with database.session() as session:
        known = await MirrorRepository(session).funnel_days(seller, old - timedelta(days=1))
    assert list(known) == [today - timedelta(days=1)]


def test_funnel_is_collected_by_interval() -> None:
    schedule = WBCoreWorker(Database(), mirror(Database()), SETTINGS)
    moment = moscow(date(2026, 11, 5), 10)

    assert schedule.due_since(MIRROR_FUNNEL, moment) == moment - timedelta(
        minutes=SETTINGS.core_mirror.funnel_interval_minutes
    )
