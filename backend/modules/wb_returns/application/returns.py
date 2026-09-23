import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry, SubscriptionService
from backend.modules.notifications.domain import Invite
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_returns.application.view import (
    ClaimView,
    RefreshRequest,
    ReturnsOverview,
    ReturnsView,
    ReturnView,
)
from backend.modules.wb_returns.domain import RETURN_READY, RETURN_TRANSIT
from backend.modules.wb_returns.infrastructure.postgres import RefreshRequestModel, ReturnModel, ReturnsRepository


class NotificationBotMissingError(Exception):
    """Бот, через который говорит автоматизация, ещё не зарегистрирован."""


class ReturnsService:
    """Что интерфейс просит у возвратов: страница кабинета, ссылка на бота, «Обновить»."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        returns: ReturnsRepository,
        bots: BotRegistry,
        subscriptions: SubscriptionService,
        *,
        bot_code: str,
        history_limit: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.returns = returns
        self.bots = bots
        self.subscriptions = subscriptions
        self.bot_code = bot_code
        self.history_limit = history_limit

    async def overview(self) -> ReturnsOverview:
        enrolled = await self.returns.tracked_seller_ids()
        last_success_at, failing = await self.returns.collection_summary(enrolled)
        return ReturnsOverview(seller_count=len(enrolled), last_success_at=last_success_at, failing=failing)

    async def view(self, seller_id: uuid.UUID) -> ReturnsView:
        seller_name = await self._enrolled(seller_id)
        tracked = await self.returns.tracked(seller_id)
        active = await self.returns.active_returns(seller_id)
        views = [self._return(model) for model in active]
        history = [
            self._return(model) for model in await self.returns.returns_history(seller_id, limit=self.history_limit)
        ]
        claims = [self._claim(model) for model in await self.returns.open_claims(seller_id)]
        claims_history = [
            self._claim(model) for model in await self.returns.claims_history(seller_id, limit=self.history_limit)
        ]
        return ReturnsView(
            seller_id=seller_id,
            seller_name=seller_name,
            collected_at=tracked.collected_at if tracked else None,
            collection_error=tracked.collection_error if tracked else None,
            ready=tuple(item for item in views if item.item.status_key == RETURN_READY),
            transit=tuple(item for item in views if item.item.status_key == RETURN_TRANSIT),
            other_active=tuple(item for item in views if item.item.status_key not in (RETURN_READY, RETURN_TRANSIT)),
            history=tuple(history),
            claims=tuple(claims),
            claims_history=tuple(claims_history),
        )

    async def invite_link(self, seller_id: uuid.UUID, created_by: uuid.UUID | None = None) -> Invite:
        """Персональная ссылка на бота для уведомлений этого кабинета."""
        seller_name = await self._enrolled(seller_id)
        try:
            bot = await self.bots.by_code(self.bot_code)
        except BotNotFoundError as error:
            raise NotificationBotMissingError(self.bot_code) from error
        return await self.subscriptions.create_invite(
            bot, seller_id=seller_id, seller_name=seller_name, created_by=created_by
        )

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequest:
        await self._enrolled(seller_id)
        request = await self.returns.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.returns.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if seller_id not in await self.returns.tracked_seller_ids():
            raise SellerNotFoundError
        return seller.name

    def _return(self, model: ReturnModel) -> ReturnView:
        return ReturnView(
            item=self.returns.to_item(model),
            status_changed_at=model.status_changed_at,
            first_seen_at=model.first_seen_at,
        )

    def _claim(self, model) -> ClaimView:  # noqa: ANN001 — ClaimModel
        return ClaimView(claim=self.returns.to_claim(model), first_seen_at=model.first_seen_at)

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> RefreshRequest:
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at,
            finished_at=request.finished_at,
            error=request.error,
        )
