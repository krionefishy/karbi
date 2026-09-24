import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    MIRROR_CATALOG,
    MIRROR_CHATS,
    MIRROR_ORDERS,
    MIRROR_REMAINS,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    MIRROR_SUPPLIES,
    ChatEvent,
    FbsOrder,
    ReviewFact,
    SellerWarehouse,
    StockFact,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.wb import (
    CatalogCard,
    FeedbackAggregation,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBPermanentError,
    WBTemporaryError,
    WBWarehouseRemainsClient,
)
from backend.storage.pg import Database

NO_RATINGS = (0, 0, 0, 0, 0)
NO_MEDIA = (0, 0)
# Живой список заданий WB помнит столько; что старше, лежит в архиве помесячно.
LIVE_ORDERS_MONTHS = 3
# Свежие задания перечитываются с таким запасом: в поставку их кладут не сразу.
ORDERS_OVERLAP = timedelta(days=3)


class SellerGoneError(Exception):
    """Селлера удалили или заархивировали, пока шёл сбор: записывать некому."""


@dataclass(frozen=True, slots=True)
class CatalogOutcome:
    active: int
    archived: int
    archived_available: bool


@dataclass(frozen=True, slots=True)
class StocksOutcome:
    articles: int
    warehouses: int


@dataclass(frozen=True, slots=True)
class RemainsOutcome:
    barcodes: int
    rows: int


@dataclass(frozen=True, slots=True)
class ReviewsOutcome:
    articles: int
    feedbacks: int


@dataclass(frozen=True, slots=True)
class OrdersOutcome:
    live: int
    archived: int
    months: int


@dataclass(frozen=True, slots=True)
class SuppliesOutcome:
    supplies: int
    warehouses: int
    offices: int


@dataclass(frozen=True, slots=True)
class ChatsOutcome:
    pages: int
    events: int
    caught_up: bool


class MirrorService:
    """Зеркало WB по селлеру: каталог, остатки, отзывы, задания и поставки FBS, чаты.

    Каждая операция — один селлер, три фазы: отметить попытку, сходить в WB без
    открытой сессии, записать результат. Сессия на время сети закрыта нарочно:
    каталог под троттлингом читается минутами, а транзакция на это время
    держала бы блокировки.

    Ошибки WB записываются в состояние и поднимаются дальше: расписание и
    консьюмер события решают сами, повторять сразу или через паузу.
    """

    def __init__(
        self,
        database: Database,
        *,
        content: WBContentClient,
        analytics: WBAnalyticsClient,
        marketplace: WBMarketplaceClient,
        feedbacks: WBFeedbackClient,
        chats: WBChatClient,
        remains: WBWarehouseRemainsClient | None = None,
        orders_history_months: int = 6,
        chats_history_days: int = 90,
        chats_pages_per_run: int = 100,
    ) -> None:
        self.database = database
        self.content = content
        self.analytics = analytics
        self.marketplace = marketplace
        self.feedbacks = feedbacks
        self.chats = chats
        self.remains = remains
        self.orders_history_months = orders_history_months
        self.chats_history_days = chats_history_days
        self.chats_pages_per_run = chats_pages_per_run
        self.logger = logging.getLogger("wb.core.mirror")

    # --- catalog ------------------------------------------------------------------

    async def sync_catalog(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CatalogOutcome:
        stamp = now or datetime.now(UTC)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_CATALOG, stamp)
            await SellerRepository(session).set_sync_status(seller_id, "syncing")
            await session.commit()
        try:
            catalog = await self.content.get_catalog(str(seller_id))
            async with self.database.session() as session:
                sellers = SellerRepository(session)
                mirror = MirrorRepository(session)
                await self._ensure_alive(session, seller_id)
                # Расписание и консьюмер события могут синхронизировать одного
                # селлера одновременно; две записи каталога вперемешку — тупик
                # в Postgres, поэтому запись идёт по очереди.
                await mirror.lock_catalog(seller_id)
                await sellers.upsert_catalog(
                    seller_id,
                    active=catalog.active,
                    archived=catalog.archived,
                    archived_available=catalog.archived_available,
                )
                await sellers.set_sync_status(seller_id, "success")
                await mirror.mark_collected(seller_id, MIRROR_CATALOG, now=stamp)
                await session.commit()
        except SellerGoneError:
            raise
        except Exception as error:
            # Любая неудача — «ошибка» с текстом, а не «идёт синхронизация»
            # навсегда и не «в очереди» без события: по этим двум статусам
            # интерфейс ждёт результата и опрашивает сервер.
            await self._fail(seller_id, MIRROR_CATALOG, str(error) or error.__class__.__name__, sync_status="error")
            raise
        outcome = CatalogOutcome(len(catalog.active), len(catalog.archived), catalog.archived_available)
        self.logger.info("catalog_synced", extra={"seller_id": str(seller_id), **asdict(outcome)})
        return outcome

    # --- stocks -------------------------------------------------------------------

    async def collect_stocks(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> StocksOutcome:
        """FBO из отчёта аналитики, FBS обходом складов продавца по chrtId.

        Обе половины пишутся вместе или не пишутся вовсе: сбой Marketplace не
        должен затирать прежние FBS нулями — нули читались бы как «товар
        кончился».
        """
        stamp = now or datetime.now(UTC)
        seller_key = str(seller_id)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_STOCKS, stamp)
            await session.commit()
            article_of = await SellerRepository(session).list_chrt_articles(seller_id)
        try:
            fbo: dict[str, list[int]] = {article: [0, 0] for article in set(article_of.values())}
            for row in await self.analytics.stocks(seller_key):
                values = fbo.setdefault(row.article, [0, 0])
                values[0] += row.quantity
                # Отчёт больше не отдаёт quantityFull: складываем сами, с товаром
                # в пути к клиенту и от клиента.
                values[1] += row.quantity + row.in_way_to_client + row.in_way_from_client
            warehouses, fbs, per_warehouse = await self._collect_fbs(seller_key, article_of)
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_STOCKS, str(error))
            raise
        facts = [
            StockFact(article, values[0], values[1], fbs.get(article, 0)) for article, values in sorted(fbo.items())
        ]
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            mirror = MirrorRepository(session)
            await mirror.replace_stocks(seller_id, facts, per_warehouse, now=stamp)
            await mirror.mark_collected(seller_id, MIRROR_STOCKS, now=stamp)
            await session.commit()
        outcome = StocksOutcome(len(facts), warehouses)
        self.logger.info("stocks_collected", extra={"seller_id": seller_key, **asdict(outcome)})
        return outcome

    async def _collect_fbs(
        self, seller_key: str, article_of: Mapping[int, str]
    ) -> tuple[int, dict[str, int], dict[tuple[str, int], int]]:
        """Заявленный остаток по складам продавца: суммой на карточку и в разрезе.

        Без размеров в каталоге спрашивать нечего, без складов — негде; и то и
        другое честно означает ноль FBS, а не «не собрали».
        """
        if not article_of:
            return 0, {}, {}
        warehouses = await self.marketplace.warehouses(seller_key)
        chrt_ids = list(article_of)
        totals: dict[str, int] = defaultdict(int)
        per_warehouse: dict[tuple[str, int], int] = defaultdict(int)
        for warehouse in warehouses:
            declared = await self.marketplace.stocks(seller_key, warehouse.id, chrt_ids)
            for chrt_id, amount in declared.items():
                article = article_of.get(chrt_id)
                if article is not None:
                    totals[article] += amount
                    per_warehouse[(article, warehouse.id)] += amount
        return len(warehouses), dict(totals), dict(per_warehouse)

    # --- остатки по складам WB --------------------------------------------------

    async def collect_remains(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> RemainsOutcome:
        """Отчёт «Остатки на складах» целиком — баркод × склад WB.

        Пустой ответ — тоже ответ: у кабинета без FBO ничего не лежит, и
        прежние строки уходят. Ошибка WB прежние строки не трогает.
        """
        if self.remains is None:
            raise WBPermanentError("Отчёт об остатках по складам WB не подключён к зеркалу")
        stamp = now or datetime.now(UTC)
        seller_key = str(seller_id)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_REMAINS, stamp)
            await session.commit()
        try:
            remains = await self.remains.remains(seller_key)
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_REMAINS, str(error))
            raise
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            mirror = MirrorRepository(session)
            await mirror.replace_remains(seller_id, remains, now=stamp)
            await mirror.mark_collected(seller_id, MIRROR_REMAINS, now=stamp)
            await session.commit()
        outcome = RemainsOutcome(len({remain.barcode for remain in remains}), len(remains))
        self.logger.info("remains_collected", extra={"seller_id": seller_key, **asdict(outcome)})
        return outcome

    # --- reviews ------------------------------------------------------------------

    async def collect_reviews(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> ReviewsOutcome:
        """Отзывы по звёздам на каждую известную карточку; нули — тоже факт.

        Карточка, которой нет в каталоге, но по которой пришли отзывы,
        заводится в каталоге как `feedback_only` — иначе её отзывы не к чему
        привязать.
        """
        stamp = now or datetime.now(UTC)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_REVIEWS, stamp)
            await session.commit()
        try:
            aggregation = await self.feedbacks.aggregate(str(seller_id))
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_REVIEWS, str(error))
            raise
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            sellers = SellerRepository(session)
            known = {article.article for article in await sellers.list_articles(seller_id)}
            unknown = self._unknown_cards(aggregation, known)
            await sellers.ensure_feedback_articles(seller_id, unknown)
            articles = known | {card.article for card in unknown}
            facts = [
                ReviewFact(
                    article=article,
                    ratings=aggregation.counts.get(article, NO_RATINGS),
                    with_photo=aggregation.media.get(article, NO_MEDIA)[0],
                    with_video=aggregation.media.get(article, NO_MEDIA)[1],
                )
                for article in sorted(articles)
            ]
            mirror = MirrorRepository(session)
            await mirror.replace_reviews(seller_id, facts, now=stamp)
            await mirror.mark_collected(seller_id, MIRROR_REVIEWS, now=stamp)
            await session.commit()
        outcome = ReviewsOutcome(len(facts), aggregation.feedback_count)
        self.logger.info("reviews_collected", extra={"seller_id": str(seller_id), **asdict(outcome)})
        return outcome

    # --- сборочные задания ------------------------------------------------------

    async def collect_orders(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> OrdersOutcome:
        """Живые задания с перекрытием и архив по месяцам, которых ещё нет.

        Живое окно начинается от последнего известного задания минус запас:
        свежие задания перечитываются, пока у них не появится поставка. Архив
        неизменяем, месяц читается один раз и отмечается.
        """
        stamp = now or datetime.now(UTC)
        seller_key = str(seller_id)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_ORDERS, stamp)
            await session.commit()
            mirror = MirrorRepository(session)
            latest = await mirror.latest_order_at(seller_id)
            done = await mirror.archive_months_done(seller_id)
        live_floor = stamp - timedelta(days=30 * LIVE_ORDERS_MONTHS)
        live_from = max(latest - ORDERS_OVERLAP, live_floor) if latest else live_floor
        months = [month for month in self._archive_months(stamp) if month not in done]
        try:
            live = await self.marketplace.orders(seller_key, date_from=live_from, date_to=stamp)
            archived: list[FbsOrder] = []
            for year, month in months:
                archived.extend(await self.marketplace.archive_orders(seller_key, year, month))
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_ORDERS, str(error))
            raise
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            mirror = MirrorRepository(session)
            await mirror.upsert_orders(seller_id, live, now=stamp)
            await mirror.upsert_orders(seller_id, archived, now=stamp)
            for year, month in months:
                await mirror.mark_archive_month(seller_id, year, month, now=stamp)
            await mirror.mark_collected(seller_id, MIRROR_ORDERS, now=stamp)
            await session.commit()
        outcome = OrdersOutcome(len(live), len(archived), len(months))
        self.logger.info("orders_collected", extra={"seller_id": seller_key, **asdict(outcome)})
        return outcome

    def _archive_months(self, now: datetime) -> list[tuple[int, int]]:
        """Месяцы от глубины истории до границы живого списка, старшие первыми."""
        first = now - timedelta(days=30 * self.orders_history_months)
        last = now - timedelta(days=30 * LIVE_ORDERS_MONTHS)
        year, month = first.year, first.month
        months: list[tuple[int, int]] = []
        while (year, month) <= (last.year, last.month):
            months.append((year, month))
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return months

    # --- поставки, склады, объекты ------------------------------------------------

    async def collect_supplies(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> SuppliesOutcome:
        """Поставки кабинета целиком, склады продавца и справочник объектов WB.

        Поставка меняется после создания (закрытие, скан QR), поэтому список
        перечитывается весь: он идёт курсором от первой, а фильтра по дате у
        метода нет. Объекты — общий справочник, но спросить его можно только
        ключом, поэтому обновляется тем кабинетом, который сейчас собираем.
        """
        stamp = now or datetime.now(UTC)
        seller_key = str(seller_id)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_SUPPLIES, stamp)
            await session.commit()
        try:
            warehouses = [
                SellerWarehouse(item.id, item.name, item.office_id)
                for item in await self.marketplace.warehouses(seller_key)
            ]
            supplies = await self.marketplace.supplies(seller_key)
            offices = await self.marketplace.offices(seller_key)
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_SUPPLIES, str(error))
            raise
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            mirror = MirrorRepository(session)
            await mirror.replace_seller_warehouses(seller_id, warehouses, now=stamp)
            await mirror.upsert_supplies(seller_id, supplies, now=stamp)
            await mirror.upsert_offices(offices, now=stamp)
            await mirror.mark_collected(seller_id, MIRROR_SUPPLIES, now=stamp)
            await session.commit()
        outcome = SuppliesOutcome(len(supplies), len(warehouses), len(offices))
        self.logger.info("supplies_collected", extra={"seller_id": seller_key, **asdict(outcome)})
        return outcome

    # --- чаты с покупателями ------------------------------------------------------

    async def collect_chats(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> ChatsOutcome:
        """Лента событий чатов от сохранённого курсора до конца или до потолка страниц.

        Каждая страница пишется вместе с курсором своей транзакцией: сбой на
        сотой странице истории не заставляет перечитывать первые девяносто
        девять. Потолок страниц — чтобы первый сбор кабинета не держал воркер:
        курсор сохранён, остальное дочитает следующий проход. Такой проход
        успешен, но «дочитано до конца» отмечается только на пустой странице.
        """
        stamp = now or datetime.now(UTC)
        seller_key = str(seller_id)
        async with self.database.session() as session:
            await self._start(session, seller_id, MIRROR_CHATS, stamp)
            await session.commit()
            saved = await MirrorRepository(session).chat_cursor(seller_id)
        cursor = saved.next if saved else int((stamp - timedelta(days=self.chats_history_days)).timestamp() * 1000)
        pages = events = 0
        caught_up = False
        try:
            while pages < self.chats_pages_per_run and not caught_up:
                page = await self.chats.events(seller_key, after=cursor)
                pages += 1
                events += len(page.events)
                # Курсор, который не сдвинулся, — конец ленты: WB возвращает его же
                # на пустой странице, а на стыке может повторить последнее событие.
                caught_up = not page.events or page.next is None or page.next <= cursor
                cursor = max(cursor, page.next or cursor)
                await self._write_chat_page(seller_id, page.events, cursor, stamp, caught_up=caught_up)
        except (WBPermanentError, WBTemporaryError) as error:
            await self._fail(seller_id, MIRROR_CHATS, str(error))
            raise
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            await MirrorRepository(session).mark_collected(seller_id, MIRROR_CHATS, now=stamp)
            await session.commit()
        outcome = ChatsOutcome(pages, events, caught_up)
        self.logger.info("chats_collected", extra={"seller_id": seller_key, **asdict(outcome)})
        return outcome

    async def _write_chat_page(
        self, seller_id: uuid.UUID, events: list[ChatEvent], cursor: int, stamp: datetime, *, caught_up: bool
    ) -> None:
        async with self.database.session() as session:
            await self._ensure_alive(session, seller_id)
            mirror = MirrorRepository(session)
            prompted = await mirror.chats_with_review_prompt(seller_id, {event.chat_id for event in events})
            kept: list[ChatEvent] = []
            for event in events:
                if event.review_prompt:
                    prompted.add(event.chat_id)
                # Слова покупателя вне диалогов об отзыве отчётам не нужны — остаётся факт сообщения.
                # Тексты продавца хранятся всегда: по ним историю можно переразметить, если WB
                # сменит текст автосообщения.
                private = event.sender == CHAT_SENDER_CLIENT and event.chat_id not in prompted
                kept.append(replace(event, text=None) if private else event)
            await mirror.insert_chat_events(seller_id, kept, now=stamp)
            await mirror.save_chat_cursor(seller_id, cursor, tail_reached_at=stamp if caught_up else None)
            await session.commit()

    @staticmethod
    def _unknown_cards(aggregation: FeedbackAggregation, known: set[str]) -> list[CatalogCard]:
        return [
            CatalogCard(
                article=product.article,
                vendor_code=product.vendor_code,
                name=product.name,
                imt_id=product.imt_id,
            )
            for article, product in aggregation.products.items()
            if article not in known
        ]

    # --- phases -------------------------------------------------------------------

    async def _start(self, session, seller_id: uuid.UUID, kind: str, now: datetime) -> None:
        seller = await SellerRepository(session).get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerGoneError(str(seller_id))
        await MirrorRepository(session).record_attempt(seller_id, kind, now=now)

    async def _ensure_alive(self, session, seller_id: uuid.UUID) -> None:
        """Архивация или удаление могли прийти, пока мы говорили с WB.

        Запись после них воскресила бы то, что только что убрали.
        """
        seller = await SellerRepository(session).get(seller_id)
        if seller is None or seller.archived_at is not None:
            await session.rollback()
            raise SellerGoneError(str(seller_id))

    async def _fail(self, seller_id: uuid.UUID, kind: str, error: str, *, sync_status: str | None = None) -> None:
        self.logger.warning(
            "mirror_collection_failed", extra={"seller_id": str(seller_id), "kind": kind, "error": error}
        )
        async with self.database.session() as session:
            await MirrorRepository(session).mark_failed(seller_id, kind, error)
            if sync_status is not None:
                await SellerRepository(session).set_sync_status(seller_id, sync_status, error)
            await session.commit()
