import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry, SubscriptionService
from backend.modules.notifications.domain import Invite
from backend.modules.wb_card_documents.application.notifications import remaining_days
from backend.modules.wb_card_documents.application.view import (
    CardDocumentsOverview,
    DocumentsBoard,
    DocumentsRow,
    DocumentsSummary,
    RefreshRequest,
)
from backend.modules.wb_card_documents.domain import STATES, rank
from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository, RefreshRequestModel
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository


class NotificationBotMissingError(Exception):
    """Бот документов ещё не заведён: ссылку на подписку выдать нельзя."""


class UnknownStateError(ValueError):
    pass


class CardDocumentsService:
    """Что интерфейс спрашивает у автоматизации: карточки с состояниями, кнопка «Обновить», ссылка на бота."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        documents: CardDocumentsRepository,
        bots: BotRegistry,
        subscriptions: SubscriptionService,
        *,
        bot_code: str,
        timezone: ZoneInfo,
        stale_hours: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.documents = documents
        self.bots = bots
        self.subscriptions = subscriptions
        self.bot_code = bot_code
        self.timezone = timezone
        self.stale = timedelta(hours=stale_hours)

    async def overview(self) -> CardDocumentsOverview:
        tracked = await self.documents.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        enrolled = tracked & active
        last_success_at, failing = await self.documents.collection_summary(enrolled)
        return CardDocumentsOverview(seller_count=len(enrolled), last_success_at=last_success_at, failing=failing)

    async def board(
        self, seller_id: uuid.UUID, state: str | None = None, *, now: datetime | None = None
    ) -> DocumentsBoard:
        """All open cards, problems first; the summary always counts every card, the filter only trims `items`."""
        if state is not None and state not in STATES:
            raise UnknownStateError(state)
        await self._enrolled(seller_id)
        tracked = await self.documents.tracked(seller_id)
        moment = now or datetime.now(UTC)
        today = moment.astimezone(self.timezone).date()
        rows = [
            DocumentsRow(
                nm_id=row.nm_id,
                vendor_code=row.vendor_code,
                title=row.title,
                subject_name=row.subject_name,
                state=row.state,
                reason=row.reason,
                document_number=row.document_number,
                document_start=row.document_start,
                document_end=row.document_end,
                days_left=remaining_days(row.document_end, row.document_endless, today),
                checked_at=row.verdict_at,
                since=row.since,
            )
            for row in (await self.documents.open_states(seller_id)).values()
        ]
        rows.sort(
            key=lambda row: (
                rank(row.state),
                row.days_left if row.days_left is not None else 1_000_000,
                row.vendor_code.lower(),
                row.nm_id,
            )
        )
        counts = {name: sum(1 for row in rows if row.state == name) for name in STATES}
        collected_at = tracked.collected_at if tracked else None
        return DocumentsBoard(
            collected_at=collected_at,
            last_error=tracked.collection_error if tracked else None,
            stale=collected_at is None or collected_at < moment - self.stale,
            summary=DocumentsSummary(total=len(rows), **counts),
            items=tuple(row for row in rows if state is None or row.state == state),
        )

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None = None) -> RefreshRequest:
        await self._enrolled(seller_id)
        request = await self.documents.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.documents.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    async def invite_link(self, seller_id: uuid.UUID, created_by: uuid.UUID | None = None) -> Invite:
        """Персональная ссылка на бота документов для этого кабинета; в ссылке нет seller_id."""
        seller_name = await self._enrolled(seller_id)
        try:
            bot = await self.bots.by_code(self.bot_code)
        except BotNotFoundError as error:
            raise NotificationBotMissingError(self.bot_code) from error
        return await self.subscriptions.create_invite(
            bot, seller_id=seller_id, seller_name=seller_name, created_by=created_by
        )

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if await self.documents.tracked(seller_id) is None:
            raise SellerNotFoundError
        return seller.name

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> RefreshRequest:
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at or datetime.now(UTC),
            finished_at=request.finished_at,
            error=request.error,
        )
