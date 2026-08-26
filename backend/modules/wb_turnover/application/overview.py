import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry, SubscriptionService
from backend.modules.notifications.domain import Invite
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.infrastructure.postgres import (
    CollectionRunModel,
    RefreshRequestModel,
    TurnoverRepository,
)

SUCCESSFUL = ("success", "partial_success")


class NotificationBotMissingError(Exception):
    """The bot this automation speaks through has not been registered yet."""


@dataclass(frozen=True, slots=True)
class ArticleTurnover:
    article: str
    name: str
    photo_url: str
    stock_total: int
    stock_fbo: int
    stock_fbs: int
    avg_stock: float
    orders_count: int
    avg_daily_orders: float
    days_of_cover: float | None
    turnover_days: float | None
    stock_days: int
    status: str


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    """State of a «обновить сейчас» press, as the interface polls it."""

    status: str
    requested_at: datetime
    finished_at: datetime | None
    error: str | None

    @property
    def in_progress(self) -> bool:
        return self.status in ("queued", "running")


@dataclass(frozen=True, slots=True)
class TurnoverSnapshot:
    """The metric as of the last day it was computed for this seller."""

    date: date | None
    articles: list[ArticleTurnover]


@dataclass(frozen=True, slots=True)
class TurnoverOverview:
    seller_count: int
    last_run: CollectionRunModel | None
    last_success_at: datetime | None
    runs_last_24h: int

    @property
    def status(self) -> str:
        if self.last_run is None:
            return "idle"
        if self.last_run.status == "running":
            return "running"
        if self.last_run.status == "success":
            return "active"
        if self.last_run.status == "partial_success":
            return "degraded"
        return "failed"


class TurnoverService:
    """What the interface asks of this automation: state, numbers, invite links."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        bots: BotRegistry,
        subscriptions: SubscriptionService,
        *,
        bot_code: str,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.bots = bots
        self.subscriptions = subscriptions
        self.bot_code = bot_code

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None = None) -> RefreshRequest:
        """Queue an out-of-schedule collection for this seller.

        The API only records the wish: talking to Wildberries from a web request
        would tie a rate-limited walk over every warehouse to someone's browser.
        """
        await self._enrolled(seller_id)
        request = await self.turnover.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.turnover.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    async def _enrolled(self, seller_id: uuid.UUID) -> None:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if seller_id not in await self.turnover.tracked_seller_ids():
            raise SellerNotFoundError

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> "RefreshRequest":
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at,
            finished_at=request.finished_at,
            error=request.error,
        )

    async def overview(self) -> TurnoverOverview:
        tracked = await self.turnover.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        return TurnoverOverview(
            seller_count=len(tracked & active),
            last_run=await self.turnover.last_run(),
            last_success_at=await self.turnover.last_success_at("calculation"),
            runs_last_24h=await self.turnover.count_runs_since(datetime.now(UTC) - timedelta(hours=24)),
        )

    async def articles(self, seller_id: uuid.UUID, day: date | None = None) -> TurnoverSnapshot:
        if await self.sellers.get(seller_id) is None:
            raise SellerNotFoundError
        target = day or await self.turnover.latest_turnover_date(seller_id)
        if target is None:
            return TurnoverSnapshot(None, [])
        catalog = {article.article: article for article in await self.sellers.list_articles(seller_id)}
        rows = await self.turnover.turnover_on(seller_id, target)
        return TurnoverSnapshot(
            target,
            [
                ArticleTurnover(
                    article=row.article,
                    name=catalog[row.article].name if row.article in catalog else "",
                    photo_url=catalog[row.article].photo_url if row.article in catalog else "",
                    stock_total=row.stock_total,
                    stock_fbo=row.stock_fbo,
                    stock_fbs=row.stock_fbs,
                    avg_stock=float(row.avg_stock),
                    orders_count=row.orders_count,
                    avg_daily_orders=float(row.avg_daily_orders),
                    days_of_cover=float(row.days_of_cover) if row.days_of_cover is not None else None,
                    turnover_days=float(row.turnover_days) if row.turnover_days is not None else None,
                    stock_days=row.stock_days,
                    status=row.status,
                )
                for row in rows
            ],
        )

    async def invite_link(self, seller_id: uuid.UUID, created_by: uuid.UUID | None = None) -> Invite:
        """A personal link to the bot for this seller's notifications."""
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if seller_id not in await self.turnover.tracked_seller_ids():
            raise SellerNotFoundError
        try:
            bot = await self.bots.by_code(self.bot_code)
        except BotNotFoundError as error:
            raise NotificationBotMissingError(self.bot_code) from error
        return await self.subscriptions.create_invite(
            bot, seller_id=seller_id, seller_name=seller.name, created_by=created_by
        )
