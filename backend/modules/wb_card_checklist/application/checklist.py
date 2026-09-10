import asyncio
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_checklist.application.ports import ReviewCounts, ReviewSource, StockSource
from backend.modules.wb_card_checklist.application.report import ChecklistReportFile, render_workbook
from backend.modules.wb_card_checklist.application.view import (
    REVIEWS_NO_SNAPSHOT,
    REVIEWS_NOT_CONNECTED,
    REVIEWS_OK,
    STOCK_NOT_CONNECTED,
    STOCK_OK,
    STOCK_STALE,
    ChecklistOverview,
    ChecklistRow,
    ChecklistView,
    RefreshRequest,
)
from backend.modules.wb_card_checklist.domain import (
    ITEMS_BY_KEY,
    ArticleFacts,
    CardFacts,
    ItemKind,
    ReviewFacts,
    Thresholds,
    accepts,
    evaluate,
)
from backend.modules.wb_card_checklist.infrastructure.postgres import ChecklistRepository, RefreshRequestModel
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository


def _stock_state(stock: Mapping[str, int] | None) -> str:
    if stock is None:
        return STOCK_NOT_CONNECTED
    return STOCK_OK if stock else STOCK_STALE


def _reviews_state(totals: Mapping[str, ReviewCounts] | None) -> str:
    if totals is None:
        return REVIEWS_NOT_CONNECTED
    return REVIEWS_OK if totals else REVIEWS_NO_SNAPSHOT


class UnknownItemError(Exception):
    """No such item in the checklist."""


class ArticleNotInChecklistError(Exception):
    """The card is not in the table right now: gone from the catalog or under the stock threshold."""


class MarkRejectedError(Exception):
    """The item cannot be set this way by hand; the message says why."""


class ChecklistService:
    """What the interface asks of the checklist: the table, ticks, comments, the xlsx."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        checklist: ChecklistRepository,
        stock: StockSource,
        reviews: ReviewSource,
        *,
        thresholds: Thresholds,
        min_stock: int,
        timezone: ZoneInfo,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.checklist = checklist
        self.stock = stock
        self.reviews = reviews
        self.thresholds = thresholds
        self.min_stock = min_stock
        self.timezone = timezone

    async def overview(self) -> ChecklistOverview:
        tracked = await self.checklist.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        enrolled = tracked & active
        last_success_at, failing = await self.checklist.collection_summary(enrolled)
        return ChecklistOverview(seller_count=len(enrolled), last_success_at=last_success_at, failing=failing)

    async def view(self, seller_id: uuid.UUID) -> ChecklistView:
        seller_name = await self._enrolled(seller_id)
        tracked = await self.checklist.tracked(seller_id)
        today = datetime.now(self.timezone).date()
        stock = await self.stock.stock(seller_id, today)
        totals = await self.reviews.totals(seller_id)
        cards = await self.checklist.card_facts(seller_id)
        prices = await self.checklist.prices(seller_id)
        directory = await self.checklist.subject_characteristics(
            {card.subject_id for card in cards if card.subject_id is not None}
        )
        marks = await self.checklist.marks(seller_id)
        comments = await self.checklist.comments(seller_id)
        reviews = self._card_reviews(cards, totals or {})

        rows: list[ChecklistRow] = []
        for card in cards:
            quantity = (stock or {}).get(card.article, 0)
            if quantity < self.min_stock:
                continue
            facts = ArticleFacts(
                card=card,
                characteristics=directory.get(card.subject_id) if card.subject_id is not None else None,
                price=prices.get(card.article),
                reviews=reviews.get(card.article),
            )
            rows.append(
                ChecklistRow(
                    article=card.article,
                    vendor_code=card.vendor_code,
                    barcode=card.barcode,
                    title=card.title,
                    photo_url=card.photo_url,
                    subject_name=card.subject_name,
                    card_created_at=card.card_created_at,
                    stock=quantity,
                    items=tuple(evaluate(facts, marks.get(card.article, {}), self.thresholds)),
                    comment=comments.get(card.article, ""),
                )
            )
        # Порядок стабильный, а не «неготовые сверху»: строка, которая уезжает
        # из-под курсора после каждой галочки, мешает работать сильнее, чем помогает.
        rows.sort(key=lambda row: (row.title.lower(), row.article))
        return ChecklistView(
            seller_id=seller_id,
            seller_name=seller_name,
            collected_at=tracked.collected_at if tracked else None,
            collection_error=tracked.collection_error if tracked else None,
            stock_state=_stock_state(stock),
            reviews_state=_reviews_state(totals),
            min_stock=self.min_stock,
            rows=tuple(rows),
        )

    async def set_mark(
        self, seller_id: uuid.UUID, article: str, item: str, checked: bool, updated_by: uuid.UUID | None
    ) -> None:
        definition = ITEMS_BY_KEY.get(item)
        if definition is None:
            raise UnknownItemError(item)
        row = await self._row(seller_id, article)
        state = next(state for state in row.items if state.key == item)
        if not accepts(state, checked):
            if definition.kind is ItemKind.AUTO:
                raise MarkRejectedError("Этот пункт проверяется по данным WB, вручную он не ставится")
            raise MarkRejectedError("В данных WB этого пока нет — отмечать выполненным не на чем")
        await self.checklist.set_mark(seller_id, article, item, checked, updated_by)
        await self.session.commit()

    async def set_comment(self, seller_id: uuid.UUID, article: str, text: str, updated_by: uuid.UUID | None) -> None:
        await self._row(seller_id, article)
        await self.checklist.set_comment(seller_id, article, text, updated_by)
        await self.session.commit()

    async def export(self, seller_id: uuid.UUID) -> ChecklistReportFile:
        view = await self.view(seller_id)
        day = datetime.now(self.timezone).date()
        # Книга собирается в треде: на тысяче строк openpyxl заметно держит event loop.
        content = await asyncio.to_thread(render_workbook, view, self.timezone)
        return ChecklistReportFile(view.seller_name, day, content)

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None = None) -> RefreshRequest:
        await self._enrolled(seller_id)
        request = await self.checklist.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.checklist.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    async def _row(self, seller_id: uuid.UUID, article: str) -> ChecklistRow:
        view = await self.view(seller_id)
        row = next((row for row in view.rows if row.article == article), None)
        if row is None:
            raise ArticleNotInChecklistError(article)
        return row

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if await self.checklist.tracked(seller_id) is None:
            raise SellerNotFoundError
        return seller.name

    @staticmethod
    def _card_reviews(cards: Sequence[CardFacts], totals: Mapping[str, ReviewCounts]) -> dict[str, ReviewFacts]:
        """Reviews per card as the buyer sees them: WB shows one feed for the whole склейка.

        A card none of whose articles is in the snapshot yet gets no facts at
        all — «ещё не считали», not «ноль отзывов».
        """
        groups: dict[str, list[str]] = {}
        for card in cards:
            key = f"imt:{card.imt_id}" if card.imt_id is not None else f"nm:{card.article}"
            groups.setdefault(key, []).append(card.article)
        facts: dict[str, ReviewFacts] = {}
        for articles in groups.values():
            known = [totals[article] for article in articles if article in totals]
            if not known:
                continue
            photos = [item.with_photo for item in known if item.with_photo is not None]
            videos = [item.with_video for item in known if item.with_video is not None]
            summary = ReviewFacts(
                total=sum(item.total for item in known),
                with_photo=sum(photos) if photos else None,
                with_video=sum(videos) if videos else None,
            )
            for article in articles:
                facts[article] = summary
        return facts

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> RefreshRequest:
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at or datetime.now(UTC),
            finished_at=request.finished_at,
            error=request.error,
        )
