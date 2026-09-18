import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.wb_core.application import ChatMirror, MirrorService, OrderMirror, ReviewMirror, StockMirror
from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    MIRROR_CATALOG,
    MIRROR_CHATS,
    MIRROR_ORDERS,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    MIRROR_SUPPLIES,
    ORDER_SOURCE_ARCHIVE,
    ORDER_SOURCE_LIVE,
    REVIEW_PROMPT_PREFIX,
    ChatEvent,
    FbsOrder,
    FbsSupply,
    WbOffice,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import ChatEventModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    CatalogCard,
    CatalogSnapshot,
    ChatEventsPage,
    FBOStockRow,
    FeedbackAggregation,
    FeedbackProduct,
    Warehouse,
    WBAnalyticsClient,
    WBChatClient,
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


def order(
    order_id: int, *, supply: str | None, created: datetime, source: str = ORDER_SOURCE_LIVE, sticker: int | None = None
) -> FbsOrder:
    return FbsOrder(
        order_id=order_id,
        rid=f"eAK.r{order_id}.0.0",
        order_uid=f"r{order_id}",
        created_at=created,
        warehouse_id=KAZAN.id,
        supply_id=supply,
        office_id=15,
        nm_id=int(FAN),
        chrt_id=FAN_CHRT,
        sku="2053999917338",
        price_kopecks=107300,
        sticker_id=sticker,
        supplier_status="complete" if source == ORDER_SOURCE_ARCHIVE else None,
        wb_status="sold" if source == ORDER_SOURCE_ARCHIVE else None,
        source=source,
    )


SUPPLY = FbsSupply(
    supply_id="WB-GI-1",
    name="Поставка от 01.09.2026",
    created_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
    closed_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    scan_dt=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    destination_office_id=15,
    done=True,
    cargo_type=1,
)
KOLEDINO = WbOffice(15, "Коледино", "Подольск", "Коледино, 1")


class FakeMarketplace(WBMarketplaceClient):
    def __init__(self, declared: dict[int, dict[int, int]], *, failing: bool = False) -> None:
        super().__init__(make_gateway())
        self.declared = declared
        self.failing = failing
        self.live: list[FbsOrder] = []
        self.archive: dict[tuple[int, int], list[FbsOrder]] = {}
        self.archive_calls: list[tuple[int, int]] = []
        self.order_windows: list[tuple[datetime, datetime]] = []

    async def warehouses(self, seller_id: str) -> list[Warehouse]:
        return [KAZAN, PITER]

    async def orders(self, seller_id: str, *, date_from: datetime, date_to: datetime) -> list[FbsOrder]:
        self.order_windows.append((date_from, date_to))
        return [item for item in self.live if date_from <= item.created_at <= date_to]

    async def archive_orders(self, seller_id: str, year: int, month: int) -> list[FbsOrder]:
        self.archive_calls.append((year, month))
        return list(self.archive.get((year, month), []))

    async def supplies(self, seller_id: str) -> list[FbsSupply]:
        return [SUPPLY]

    async def offices(self, seller_id: str) -> list[WbOffice]:
        return [KOLEDINO]

    async def stocks(self, seller_id: str, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        if self.failing:
            raise WBTemporaryError("WB Marketplace API: 503")
        return {chrt: amount for chrt, amount in self.declared.get(warehouse_id, {}).items() if chrt in chrt_ids}


class FakeChats(WBChatClient):
    """Лента WB: события после курсора, по две на страницу; на пустой странице курсор возвращается тем же."""

    def __init__(self, feed: list[ChatEvent], *, failing_after: int | None = None) -> None:
        super().__init__(make_gateway())
        self.feed = feed
        self.failing_after = failing_after
        self.cursors: list[int | None] = []

    async def events(self, seller_id: str, *, after: int | None) -> ChatEventsPage:
        if self.failing_after is not None and len(self.cursors) >= self.failing_after:
            raise WBTemporaryError("WB Buyers Chat API отвечает HTTP 503")
        self.cursors.append(after)
        newer = [item for item in self.feed if after is None or millis(item.added_at) > after]
        page = newer[:2]
        return ChatEventsPage(events=page, next=millis(page[-1].added_at) if page else after)


def millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def message(
    number: int, chat: str, *, minute: int, sender: str = CHAT_SENDER_CLIENT, source: str = "ios", text: str = "Текст"
) -> ChatEvent:
    prompt = sender == CHAT_SENDER_SELLER and source == CHAT_SOURCE_PORTAL and text.startswith(REVIEW_PROMPT_PREFIX)
    return ChatEvent(
        event_id=f"event-{number}",
        chat_id=chat,
        sender=sender,
        source=source,
        added_at=datetime(2026, 9, 13, 10, minute, tzinfo=UTC),
        is_new_chat=prompt,
        review_prompt=prompt,
        nm_id=1304195061 if prompt else None,
        rid=None,
        text=text,
        has_attachments=False,
    )


def review_dialog() -> list[ChatEvent]:
    """Чужой чат, затем диалог об отзыве: автосообщение WB, наше следом и ответ покупателя."""
    prompt = f"{REVIEW_PROMPT_PREFIX}. Давайте обсудим, что не так с товаром."
    return [
        message(1, "chat-return", minute=30, text="Хочу вернуть товар, телефон +79990000000"),
        message(2, "chat-review", minute=35, sender=CHAT_SENDER_SELLER, source=CHAT_SOURCE_PORTAL, text=prompt),
        message(3, "chat-review", minute=36, sender=CHAT_SENDER_SELLER, source=CHAT_SOURCE_API, text="Ответ не нужен"),
        message(4, "chat-return", minute=40, text="Когда ответите?"),
        message(5, "chat-review", minute=50, text="Товар сломался"),
    ]


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
    chats: WBChatClient | None = None,
    chats_pages_per_run: int = 100,
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
        chats=chats or FakeChats([]),
        chats_pages_per_run=chats_pages_per_run,
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
        model = SellerModel(name="ИП Зеркало", catalog_sync_status="success", egress_status="verified")
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
    # Остатки ключуются размерами каталога: пока каталог не собран, срез остатков не берётся.
    assert await worker(database, service, at(10)).collect_due(MIRROR_STOCKS, at(10)) == 0
    assert await worker(database, service, at(10)).collect_due(MIRROR_CATALOG, at(10)) == 1

    assert await worker(database, service, at(10)).collect_due(MIRROR_STOCKS, at(10)) == 1
    # Собран в 10:00 — срез 09:00 закрыт, до 15:00 делать нечего.
    assert await worker(database, service, at(12)).collect_due(MIRROR_STOCKS, at(12)) == 0
    assert await worker(database, service, at(15)).collect_due(MIRROR_STOCKS, at(15)) == 1


async def test_a_failed_seller_waits_for_the_retry_pause(database: Database, seller: uuid.UUID) -> None:
    await mirror(database).sync_catalog(seller, now=at(9))
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

    assert await worker(database, mirror(database), at(10)).collect_due(MIRROR_CATALOG, at(10)) == 0


async def test_a_seller_without_a_servable_key_is_not_mirrored(database: Database, seller: uuid.UUID) -> None:
    """Шлюз его отвергнет, а ошибка зеркала заслонила бы настоящую причину — недоставленный ключ."""
    async with database.session() as session:
        await SellerRepository(session).set_egress_state(seller, status="key_invalid", error="rejected")
        await session.commit()

    assert await worker(database, mirror(database), at(10)).collect_due(MIRROR_CATALOG, at(10)) == 0
    async with database.session() as session:
        model = await SellerRepository(session).get(seller)
    assert model is not None and model.catalog_sync_status == "success"


async def test_a_failed_mirror_reads_as_stalled_rather_than_not_yet(database: Database, seller: uuid.UUID) -> None:
    """Иначе чек-лист обещал бы данные «в ближайший час» при отозванном ключе."""
    moment = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)
    failing = mirror(database, marketplace=FakeMarketplace({}, failing=True))
    with pytest.raises(WBTemporaryError):
        await failing.collect_stocks(seller, now=moment)

    async with database.session() as session:
        assert await StockMirror(session).stock(seller, fresh_since=moment) == {}
        assert await ReviewMirror(session).totals(seller) is None


# --- задания и поставки -----------------------------------------------------------


async def test_orders_are_mirrored_live_and_from_the_archive_once(database: Database, seller: uuid.UUID) -> None:
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    marketplace = FakeMarketplace({})
    marketplace.live = [order(1, supply=None, created=now - timedelta(days=1))]
    marketplace.archive[(2026, 4)] = [
        order(
            9,
            supply="WB-GI-old",
            created=datetime(2026, 4, 3, tzinfo=UTC),
            source=ORDER_SOURCE_ARCHIVE,
            sticker=33811984302,
        )
    ]
    service = mirror(database, marketplace=marketplace)

    first = await service.collect_orders(seller, now=now)

    assert (first.live, first.archived) == (1, 1)
    # Глубина 6 месяцев минус живые 3: архив спрошен по месяцам с марта по июнь, старшие первыми.
    assert marketplace.archive_calls == [(2026, 3), (2026, 4), (2026, 5), (2026, 6)]
    assert first.months == 4
    # Первое живое окно — три месяца назад.
    assert marketplace.order_windows[0][0] == now - timedelta(days=90)

    # Задание легло в поставку: повторный сбор перечитывает свежие и дозаполняет supply_id,
    # а архив второй раз не трогает.
    marketplace.live = [order(1, supply="WB-GI-1", created=now - timedelta(days=1))]
    second = await service.collect_orders(seller, now=now + timedelta(hours=1))

    assert second.months == 0 and len(marketplace.archive_calls) == 4
    assert marketplace.order_windows[-1][0] == now - timedelta(days=1) - timedelta(days=3)
    async with database.session() as session:
        found = await MirrorRepository(session).orders_by_keys(seller, order_ids=[1, 9])
    by_id = {item.order_id: item for item in found}
    assert by_id[1].supply_id == "WB-GI-1"
    assert (by_id[9].sticker_id, by_id[9].wb_status, by_id[9].source) == (33811984302, "sold", ORDER_SOURCE_ARCHIVE)


async def test_order_mirror_resolves_by_rid_order_id_and_sticker(database: Database, seller: uuid.UUID) -> None:
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    marketplace = FakeMarketplace({})
    marketplace.live = [order(1, supply="WB-GI-1", created=now - timedelta(days=2))]
    marketplace.archive[(2026, 5)] = [
        order(
            9,
            supply="WB-GI-1",
            created=datetime(2026, 5, 3, tzinfo=UTC),
            source=ORDER_SOURCE_ARCHIVE,
            sticker=33811984302,
        )
    ]
    service = mirror(database, marketplace=marketplace)
    await service.collect_orders(seller, now=now)
    await service.collect_supplies(seller, now=now)

    async with database.session() as session:
        traces = await OrderMirror(session).resolve(
            seller, rids=["eAK.r1.0.0"], order_ids=[9], sticker_ids=[33811984302, 5]
        )

    # Каждое найденное задание отвечает всеми своими ключами; несуществующий стикер 5 не отвечает ничем.
    assert {"eAK.r1.0.0", "1", "9", "33811984302"} <= set(traces) and "5" not in traces
    trace = traces["eAK.r1.0.0"]
    assert trace.warehouse_name == KAZAN.name
    assert trace.supply is not None and trace.supply.scan_dt == SUPPLY.scan_dt
    assert trace.destination_office_name == "Коледино"
    assert traces["33811984302"].order.order_id == 9

    # Выгрузка за неделю спрашивает о десятках тысяч заданий: у asyncpg потолок 32 767 параметров.
    async with database.session() as session:
        many = await OrderMirror(session).resolve(
            seller, rids=[f"r{i}" for i in range(20_000)], order_ids=[*range(10, 20_010), 9], sticker_ids=range(20_000)
        )
    assert "9" in many


async def test_old_orders_are_pruned_once_a_day(database: Database, seller: uuid.UUID) -> None:
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    marketplace = FakeMarketplace({})
    marketplace.archive[(2026, 4)] = [
        order(9, supply=None, created=datetime(2026, 4, 3, tzinfo=UTC), source=ORDER_SOURCE_ARCHIVE)
    ]
    await mirror(database, marketplace=marketplace).collect_orders(seller, now=now)

    keeper = WBCoreWorker(database, mirror(database), SETTINGS, now=lambda: now)
    keeper.config = replace(keeper.config, orders_retention_days=100)
    assert await keeper.prune(now) == 1
    assert await keeper.prune(now) == 0


async def test_orders_are_due_by_interval_and_supplies_daily(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database)
    await service.sync_catalog(seller, now=at(9))
    schedule = WBCoreWorker(database, service, SETTINGS)

    assert schedule.due_since(MIRROR_ORDERS, at(10)) == at(10) - timedelta(
        minutes=SETTINGS.core_mirror.orders_interval_minutes
    )
    assert schedule.due_since(MIRROR_SUPPLIES, at(10)) == at(5)

    assert await worker(database, service, at(10)).collect_due(MIRROR_ORDERS, at(10)) == 1
    assert await worker(database, service, at(10, 30)).collect_due(MIRROR_ORDERS, at(10, 30)) == 0
    assert await worker(database, service, at(11, 5)).collect_due(MIRROR_ORDERS, at(11, 5)) == 1
    assert await worker(database, service, at(10)).collect_due(MIRROR_SUPPLIES, at(10)) == 1
    assert await worker(database, service, at(12)).collect_due(MIRROR_SUPPLIES, at(12)) == 0


async def test_the_chat_feed_is_read_to_its_end_and_resumed_from_the_cursor(
    database: Database, seller: uuid.UUID
) -> None:
    feed = review_dialog()
    chats = FakeChats(feed[:3])
    service = mirror(database, chats=chats)

    outcome = await service.collect_chats(seller, now=at(17))
    assert (outcome.events, outcome.caught_up) == (3, True)
    # Первый сбор начинает не с начала ленты, а с глубины истории.
    assert chats.cursors[0] == millis(at(17) - timedelta(days=90))

    chats.feed = feed
    outcome = await service.collect_chats(seller, now=at(18))
    assert (outcome.events, outcome.caught_up) == (2, True)
    assert chats.cursors[-2] == millis(feed[2].added_at)

    async with database.session() as session:
        port = ChatMirror(session)
        events = await port.review_dialog_events(seller, since=at(0), until=at(23))
        state = await port.state(seller)
    assert [event.event_id for event in events] == ["event-2", "event-3", "event-5"]
    assert events[0].review_prompt and events[0].nm_id == 1304195061
    assert events[2].text == "Товар сломался"
    assert state is not None and state.synced_through == at(18) and state.error is None
    assert state.history_from == feed[0].added_at


async def test_client_text_outside_review_dialogs_is_not_stored(database: Database, seller: uuid.UUID) -> None:
    await mirror(database, chats=FakeChats(review_dialog())).collect_chats(seller, now=at(17))

    async with database.session() as session:
        rows = {
            row.event_id: row.text
            for row in await session.scalars(select(ChatEventModel).where(ChatEventModel.seller_id == seller))
        }
    assert rows["event-1"] is None and rows["event-4"] is None
    assert rows["event-5"] == "Товар сломался"
    # Слова продавца хранятся всегда: по ним историю можно переразметить.
    assert rows["event-3"] == "Ответ не нужен"


async def test_a_long_history_is_read_in_portions_and_is_not_called_complete(
    database: Database, seller: uuid.UUID
) -> None:
    service = mirror(database, chats=FakeChats(review_dialog()), chats_pages_per_run=1)

    outcome = await service.collect_chats(seller, now=at(17))
    assert (outcome.pages, outcome.events, outcome.caught_up) == (1, 2, False)
    async with database.session() as session:
        state = await ChatMirror(session).state(seller)
        schedule = await MirrorRepository(session).state(seller, MIRROR_CHATS)
    # Проход успешен — повтор придёт по расписанию, а не через паузу после ошибки, —
    # но до конца ленты ещё не дошли.
    assert schedule is not None and schedule.collected_at == at(17)
    assert state is not None and state.synced_through is None

    for hour in (18, 19, 20):
        outcome = await service.collect_chats(seller, now=at(hour))
    assert outcome.caught_up


async def test_a_chat_failure_keeps_the_pages_already_written(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database, chats=FakeChats(review_dialog(), failing_after=1))

    with pytest.raises(WBTemporaryError):
        await service.collect_chats(seller, now=at(17))

    async with database.session() as session:
        repository = MirrorRepository(session)
        cursor = await repository.chat_cursor(seller)
        state = await repository.state(seller, MIRROR_CHATS)
    assert cursor is not None and cursor.next == millis(review_dialog()[1].added_at)
    assert state is not None and state.collected_at is None and "503" in (state.error or "")


async def test_chats_are_due_by_interval_and_do_not_wait_for_the_catalog(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database, chats=FakeChats(review_dialog()))

    assert await worker(database, service, at(10)).collect_due(MIRROR_CHATS, at(10)) == 1
    assert await worker(database, service, at(10, 10)).collect_due(MIRROR_CHATS, at(10, 10)) == 0
    assert await worker(database, service, at(10, 35)).collect_due(MIRROR_CHATS, at(10, 35)) == 1


async def test_old_chat_events_are_pruned(database: Database, seller: uuid.UUID) -> None:
    await mirror(database, chats=FakeChats(review_dialog())).collect_chats(seller, now=at(17))
    now = at(17) + timedelta(days=200)

    keeper = WBCoreWorker(database, mirror(database), SETTINGS, now=lambda: now)
    assert await keeper.prune(now) == 5
