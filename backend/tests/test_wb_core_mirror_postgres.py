import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_core.application import MirrorService, ReviewMirror, StockMirror
from backend.modules.wb_core.domain import MIRROR_CATALOG, MIRROR_REVIEWS, MIRROR_STOCKS
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    CatalogCard,
    CatalogSnapshot,
    FBOStockRow,
    FeedbackAggregation,
    FeedbackProduct,
    Warehouse,
    WBAnalyticsClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBPermanentError,
    WBTemporaryError,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway
from backend.workers.wb_core.worker import WBCoreWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
MOSCOW = ZoneInfo("Europe/Moscow")

FAN, VACUUM = "1001", "1002"
FAN_CHRT, VACUUM_CHRT = 11, 22
KAZAN, PITER = Warehouse(501, "5. Казань наша"), Warehouse(502, "4. Питер наш")


def fbo(article: str, quantity: int, to_client: int = 0) -> FBOStockRow:
    return FBOStockRow(article, 0, -999999, "Склад WB", "", quantity, to_client, 0)


class FakeAnalytics(WBAnalyticsClient):
    def __init__(self, rows: list[FBOStockRow]) -> None:
        super().__init__(make_gateway())
        self.rows = rows

    async def stocks(self, seller_id: str) -> list[FBOStockRow]:
        return list(self.rows)


class FakeMarketplace(WBMarketplaceClient):
    def __init__(self, declared: dict[int, dict[int, int]], *, failing: bool = False) -> None:
        super().__init__(make_gateway())
        self.declared = declared
        self.failing = failing

    async def warehouses(self, seller_id: str) -> list[Warehouse]:
        return [KAZAN, PITER]

    async def stocks(self, seller_id: str, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        if self.failing:
            raise WBTemporaryError("WB Marketplace API: 503")
        return {chrt: amount for chrt, amount in self.declared.get(warehouse_id, {}).items() if chrt in chrt_ids}


class FakeFeedbacks(WBFeedbackClient):
    def __init__(self, aggregation: FeedbackAggregation) -> None:
        super().__init__(make_gateway())
        self.aggregation = aggregation

    async def aggregate(self, seller_id: str) -> FeedbackAggregation:
        return self.aggregation


class FakeContent(WBContentClient):
    def __init__(self, snapshot: CatalogSnapshot | Exception) -> None:
        super().__init__(make_gateway())
        self.snapshot = snapshot

    async def get_catalog(self, seller_id: str) -> CatalogSnapshot:
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


def card(article: str, chrt_id: int) -> CatalogCard:
    return CatalogCard(
        article=article, vendor_code=f"SKU-{article}", name=f"Товар {article}", sizes=[{"chrt_id": chrt_id, "skus": []}]
    )


def mirror(
    database: Database,
    *,
    analytics: WBAnalyticsClient | None = None,
    marketplace: WBMarketplaceClient | None = None,
    feedbacks: WBFeedbackClient | None = None,
    content: WBContentClient | None = None,
) -> MirrorService:
    return MirrorService(
        database,
        content=content
        or FakeContent(CatalogSnapshot(active=[card(FAN, FAN_CHRT)], archived=[], archived_available=True)),
        analytics=analytics or FakeAnalytics([fbo(FAN, 40, to_client=3)]),
        marketplace=marketplace or FakeMarketplace({KAZAN.id: {FAN_CHRT: 5, VACUUM_CHRT: 2}, PITER.id: {FAN_CHRT: 1}}),
        feedbacks=feedbacks
        or FakeFeedbacks(
            FeedbackAggregation(
                counts={FAN: (0, 0, 1, 2, 7), "3003": (1, 0, 0, 0, 0)},
                products={"3003": FeedbackProduct("3003", "SKU-3003", "Товар только в отзывах")},
                feedback_count=11,
                media={FAN: (4, 1)},
            )
        ),
    )


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
        model = SellerModel(name="ИП Зеркало", catalog_sync_status="success")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await SellerRepository(session).upsert_catalog(
            seller_id, active=[card(FAN, FAN_CHRT), card(VACUUM, VACUUM_CHRT)], archived=[], archived_available=True
        )
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await MirrorRepository(session).purge_seller(seller_id)
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


async def test_stocks_are_mirrored_with_zeros_for_cards_the_report_omits(database: Database, seller: uuid.UUID) -> None:
    moment = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)

    outcome = await mirror(database).collect_stocks(seller, now=moment)

    assert (outcome.articles, outcome.warehouses) == (2, 2)
    async with database.session() as session:
        facts = await StockMirror(session).stock(seller, fresh_since=moment - timedelta(days=2))
        per_warehouse = await MirrorRepository(session).fbs_warehouse_stocks(seller)
    assert facts is not None
    assert (facts[FAN].fbo_quantity, facts[FAN].fbo_quantity_full, facts[FAN].fbs_quantity) == (40, 43, 6)
    # Пылесоса в отчёте нет — это ноль FBO, а не пропуск; FBS у него только в Казани.
    assert (facts[VACUUM].fbo_quantity, facts[VACUUM].fbs_quantity, facts[VACUUM].total) == (0, 2, 2)
    assert per_warehouse == {(FAN, KAZAN.id): 5, (FAN, PITER.id): 1, (VACUUM, KAZAN.id): 2}


async def test_stock_freshness_distinguishes_never_and_stale(database: Database, seller: uuid.UUID) -> None:
    moment = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    async with database.session() as session:
        assert await StockMirror(session).stock(seller, fresh_since=moment) is None

    await mirror(database).collect_stocks(seller, now=moment)

    async with database.session() as session:
        reader = StockMirror(session)
        assert await reader.stock(seller, fresh_since=moment + timedelta(minutes=1)) == {}
        assert await reader.collected_at(seller) == moment


async def test_an_fbs_failure_keeps_the_previous_stocks(database: Database, seller: uuid.UUID) -> None:
    """Zeros written on a Marketplace outage would read as «товар кончился»."""
    first = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    await mirror(database).collect_stocks(seller, now=first)

    with pytest.raises(WBTemporaryError):
        await mirror(database, marketplace=FakeMarketplace({}, failing=True)).collect_stocks(
            seller, now=first + timedelta(hours=6)
        )

    async with database.session() as session:
        state = await MirrorRepository(session).state(seller, MIRROR_STOCKS)
        facts = await MirrorRepository(session).stocks(seller)
    assert state is not None and state.collected_at == first
    assert state.attempted_at == first + timedelta(hours=6)
    assert "503" in (state.error or "")
    assert facts[FAN].fbs_quantity == 6


async def test_reviews_are_mirrored_for_every_known_card(database: Database, seller: uuid.UUID) -> None:
    moment = datetime(2026, 9, 13, 4, 0, tzinfo=UTC)

    outcome = await mirror(database).collect_reviews(seller, now=moment)

    assert (outcome.articles, outcome.feedbacks) == (3, 11)
    async with database.session() as session:
        totals = await ReviewMirror(session).totals(seller)
        articles = {article.article: article for article in await SellerRepository(session).list_articles(seller)}
    assert totals is not None
    assert (totals[FAN].total, totals[FAN].with_photo, totals[FAN].with_video) == (10, 4, 1)
    # Известная карточка без отзывов — нули, а не отсутствие строки.
    assert (totals[VACUUM].total, totals[VACUUM].with_photo) == (0, 0)
    # Карточка только из отзывов заводится в каталоге, иначе отзывы не к чему привязать.
    assert totals["3003"].total == 1
    assert articles["3003"].state == "feedback_only"


async def test_catalog_sync_updates_the_registry_status(database: Database, seller: uuid.UUID) -> None:
    moment = datetime(2026, 9, 13, 2, 30, tzinfo=UTC)

    await mirror(database).sync_catalog(seller, now=moment)

    async with database.session() as session:
        model = await SellerRepository(session).get(seller)
        state = await MirrorRepository(session).state(seller, MIRROR_CATALOG)
        articles = {article.article: article for article in await SellerRepository(session).list_articles(seller)}
    assert model is not None and model.catalog_sync_status == "success"
    assert state is not None and state.collected_at == moment
    # Пылесос из ответа пропал — карточка остаётся, но уже не как товар каталога.
    assert (articles[FAN].state, articles[VACUUM].state) == ("active", "feedback_only")


async def test_a_rejected_key_is_visible_in_the_registry(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database, content=FakeContent(WBPermanentError("WB Content API: ключ недействителен")))

    with pytest.raises(WBPermanentError):
        await service.sync_catalog(seller)

    async with database.session() as session:
        model = await SellerRepository(session).get(seller)
        state = await MirrorRepository(session).state(seller, MIRROR_CATALOG)
    assert model is not None and model.catalog_sync_status == "error"
    assert "недействителен" in (model.catalog_sync_error or "")
    assert state is not None and state.collected_at is None and "недействителен" in (state.error or "")


async def test_purging_the_seller_drops_the_mirror(database: Database, seller: uuid.UUID) -> None:
    await mirror(database).collect_stocks(seller)
    await mirror(database).collect_reviews(seller)

    async with database.session() as session:
        assert await SellerRepository(session).delete(seller)
        await session.commit()

    async with database.session() as session:
        repository = MirrorRepository(session)
        assert await repository.stocks(seller) == {}
        assert await repository.reviews(seller) == {}
        assert await repository.state(seller, MIRROR_STOCKS) is None


# --- worker schedule -------------------------------------------------------------


def worker(database: Database, service: MirrorService, moment: datetime) -> WBCoreWorker:
    return WBCoreWorker(database, service, SETTINGS, now=lambda: moment)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 13, hour, minute, tzinfo=MOSCOW).astimezone(UTC)


def test_due_since_takes_the_last_passed_slot() -> None:
    schedule = WBCoreWorker(Database(), mirror(Database()), SETTINGS)

    assert schedule.due_since(MIRROR_STOCKS, at(10)) == at(9)
    assert schedule.due_since(MIRROR_STOCKS, at(9)) == at(9)
    # До первого среза дня — последний срез вчерашнего, иначе полночь собирала бы всех заново.
    assert schedule.due_since(MIRROR_STOCKS, at(2)) == at(21) - timedelta(days=1)
    assert schedule.due_since(MIRROR_CATALOG, at(2)) == at(2, 30) - timedelta(days=1)
    assert schedule.due_since(MIRROR_REVIEWS, at(4)) == at(4)


async def test_a_never_collected_seller_is_due_at_once_and_then_waits_for_the_next_slot(
    database: Database, seller: uuid.UUID
) -> None:
    service = mirror(database)

    assert await worker(database, service, at(10)).collect_due(MIRROR_STOCKS, at(10)) == 1
    # Собран в 10:00 — срез 09:00 закрыт, до 15:00 делать нечего.
    assert await worker(database, service, at(12)).collect_due(MIRROR_STOCKS, at(12)) == 0
    assert await worker(database, service, at(15)).collect_due(MIRROR_STOCKS, at(15)) == 1


async def test_a_failed_seller_waits_for_the_retry_pause(database: Database, seller: uuid.UUID) -> None:
    failing = mirror(database, marketplace=FakeMarketplace({}, failing=True))

    assert await worker(database, failing, at(10)).collect_due(MIRROR_STOCKS, at(10)) == 0
    async with database.session() as session:
        state = await MirrorRepository(session).state(seller, MIRROR_STOCKS)
    assert state is not None and state.collected_at is None and state.error

    pause = timedelta(minutes=SETTINGS.core_mirror.retry_minutes + 1)
    assert await worker(database, mirror(database), at(10, 5)).collect_due(MIRROR_STOCKS, at(10, 5)) == 0
    assert await worker(database, mirror(database), at(10) + pause).collect_due(MIRROR_STOCKS, at(10) + pause) == 1


async def test_an_archived_seller_is_not_mirrored(database: Database, seller: uuid.UUID) -> None:
    async with database.session() as session:
        assert await SellerRepository(session).archive(seller)
        await session.commit()

    assert await worker(database, mirror(database), at(10)).collect_due(MIRROR_STOCKS, at(10)) == 0
