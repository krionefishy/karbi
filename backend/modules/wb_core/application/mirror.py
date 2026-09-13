import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from backend.modules.wb_core.domain import (
    MIRROR_CATALOG,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    ReviewFact,
    StockFact,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.wb import (
    CatalogCard,
    FeedbackAggregation,
    WBAnalyticsClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBPermanentError,
    WBTemporaryError,
)
from backend.storage.pg import Database

NO_RATINGS = (0, 0, 0, 0, 0)
NO_MEDIA = (0, 0)


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
class ReviewsOutcome:
    articles: int
    feedbacks: int


class MirrorService:
    """Зеркало WB по селлеру: каталог, остатки, отзывы.

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
    ) -> None:
        self.database = database
        self.content = content
        self.analytics = analytics
        self.marketplace = marketplace
        self.feedbacks = feedbacks
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
        except WBPermanentError as error:
            # Ключ отозван или права урезаны: статус синка нужен реестру,
            # чтобы админка показала причину, а не «идёт синхронизация».
            await self._fail(seller_id, MIRROR_CATALOG, str(error), sync_status="error")
            raise
        except WBTemporaryError as error:
            await self._fail(seller_id, MIRROR_CATALOG, str(error), sync_status="queued")
            raise
        async with self.database.session() as session:
            sellers = SellerRepository(session)
            await self._ensure_alive(session, seller_id)
            await sellers.upsert_catalog(
                seller_id,
                active=catalog.active,
                archived=catalog.archived,
                archived_available=catalog.archived_available,
            )
            await sellers.set_sync_status(seller_id, "success")
            await MirrorRepository(session).mark_collected(seller_id, MIRROR_CATALOG, now=stamp)
            await session.commit()
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
                await SellerRepository(session).set_sync_status(
                    seller_id, sync_status, error if sync_status == "error" else None
                )
            await session.commit()
