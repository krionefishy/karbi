import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ChatMirror, SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_review_chats.application.report import ReviewChatsReportFile, build_workbook
from backend.modules.wb_review_chats.application.view import (
    Baseline,
    DaySummary,
    DialogView,
    ReviewChatsOverview,
    ReviewChatsView,
)
from backend.modules.wb_review_chats.domain import (
    FOLLOW_UP_WITHIN,
    GROUP_BEFORE,
    GROUP_EARLY,
    GROUP_FOLLOWED,
    GROUP_MISSED,
    GROUPS,
    OUTCOMES,
    Dialog,
    build_dialogs,
    summarize,
)
from backend.modules.wb_review_chats.infrastructure.postgres import ReviewChatsRepository


class ReviewChatsQueryError(Exception):
    """Запрос, по которому нечего показать: перевёрнутый или слишком длинный период, незнакомый фильтр."""


class ReviewChatsService:
    """Отчёт по диалогам после отзыва. Считается при чтении из зеркала чатов wb_core."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        tracked: ReviewChatsRepository,
        *,
        timezone: ZoneInfo,
        reply_window_hours: int,
        max_period_days: int,
        baseline_days: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.tracked = tracked
        self.chats = ChatMirror(session)
        self.timezone = timezone
        self.reply_window_hours = reply_window_hours
        self.max_period_days = max_period_days
        self.baseline_days = baseline_days

    async def overview(self) -> ReviewChatsOverview:
        enrolled = await self.tracked.tracked_seller_ids() & {seller.id for seller in await self.sellers.list_sellers()}
        synced: list[datetime] = []
        failing = 0
        for seller_id in enrolled:
            state = await self.chats.state(seller_id)
            if state is None:
                continue
            failing += 1 if state.error else 0
            if state.synced_through is not None:
                synced.append(state.synced_through)
        return ReviewChatsOverview(
            seller_count=len(enrolled), last_success_at=max(synced) if synced else None, failing=failing
        )

    async def view(
        self,
        seller_id: uuid.UUID,
        date_from: date,
        date_to: date,
        *,
        group: str | None = None,
        outcome: str | None = None,
        page: int = 1,
        page_size: int | None = None,
        now: datetime | None = None,
    ) -> ReviewChatsView:
        """Сводка и дни — по всему периоду; фильтры и страница режут только список диалогов."""
        if date_from > date_to:
            raise ReviewChatsQueryError("Начало периода позже его конца")
        if (date_to - date_from).days >= self.max_period_days:
            raise ReviewChatsQueryError(f"Период длиннее {self.max_period_days} дней")
        if group is not None and group not in GROUPS:
            raise ReviewChatsQueryError("Незнакомая группа диалогов")
        if outcome is not None and outcome not in OUTCOMES:
            raise ReviewChatsQueryError("Незнакомый исход диалога")
        seller_name = await self._enrolled(seller_id)
        state = await self.chats.state(seller_id)
        # Молчание — факт только до момента, докуда прочитана лента: отставший
        # воркер не должен превращать непрочитанные ответы в «не ответил».
        moment = now or datetime.now(UTC)
        if state is not None and state.read_through is not None:
            moment = min(moment, state.read_through)
        launch_at = await self.chats.launch_at(seller_id, within=FOLLOW_UP_WITHIN)
        dialogs = await self._dialogs(seller_id, date_from, date_to, moment, launch_at)
        baseline = None
        if launch_at is not None:
            # Окно до запуска — до дня запуска, не включая его: в день запуска группы уже смешаны.
            launch_day = launch_at.astimezone(self.timezone).date()
            baseline_from, baseline_to = launch_day - timedelta(days=self.baseline_days), launch_day - timedelta(days=1)
            before = await self._dialogs(seller_id, baseline_from, baseline_to, moment, launch_at)
            baseline = Baseline(baseline_from, baseline_to, summarize(before))
        listed = [
            dialog
            for dialog in dialogs
            if (group is None or dialog.group == group) and (outcome is None or dialog.outcome == outcome)
        ]
        size = page_size or max(len(listed), 1)
        shown = listed[(page - 1) * size : page * size]
        names = await self.sellers.article_names(seller_id, [str(dialog.nm_id) for dialog in shown if dialog.nm_id])
        return ReviewChatsView(
            seller_id=seller_id,
            seller_name=seller_name,
            date_from=date_from,
            date_to=date_to,
            reply_window_hours=self.reply_window_hours,
            launch_at=launch_at,
            followed=summarize(dialogs, GROUP_FOLLOWED),
            early=summarize(dialogs, GROUP_EARLY),
            missed=summarize(dialogs, GROUP_MISSED),
            before=summarize(dialogs, GROUP_BEFORE),
            after_launch=summarize(dialog for dialog in dialogs if dialog.group != GROUP_BEFORE),
            baseline=baseline,
            days=self._days(dialogs),
            dialogs=tuple(DialogView(dialog, names.get(str(dialog.nm_id), "")) for dialog in shown),
            page=page,
            page_size=size,
            total_dialogs=len(listed),
            history_from=state.history_from if state else None,
            synced_through=state.synced_through if state else None,
            collection_error=state.error if state else None,
        )

    async def export(self, seller_id: uuid.UUID, date_from: date, date_to: date) -> ReviewChatsReportFile:
        view = await self.view(seller_id, date_from, date_to)
        return ReviewChatsReportFile(
            seller_name=view.seller_name,
            date_from=date_from,
            date_to=date_to,
            content=build_workbook(view, self.timezone),
        )

    # --- helpers ----------------------------------------------------------------------

    async def _dialogs(
        self, seller_id: uuid.UUID, date_from: date, date_to: date, now: datetime, launch_at: datetime | None
    ) -> list[Dialog]:
        since = datetime.combine(date_from, time.min, tzinfo=self.timezone).astimezone(UTC)
        until = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=self.timezone).astimezone(UTC)
        events = await self.chats.review_dialog_events(seller_id, since=since, until=until)
        return build_dialogs(
            events,
            since=since,
            until=until,
            window=timedelta(hours=self.reply_window_hours),
            now=now,
            launch_at=launch_at,
        )

    def _days(self, dialogs: list[Dialog]) -> tuple[DaySummary, ...]:
        by_day: dict[date, list[Dialog]] = {}
        for dialog in dialogs:
            by_day.setdefault(dialog.prompt_at.astimezone(self.timezone).date(), []).append(dialog)
        return tuple(
            DaySummary(
                day,
                summarize(items),
                summarize(items, GROUP_FOLLOWED),
                summarize(items, GROUP_EARLY),
                summarize(items, GROUP_MISSED),
            )
            for day, items in sorted(by_day.items(), reverse=True)
        )

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if not await self.tracked.is_tracked(seller_id):
            raise SellerNotFoundError
        return seller.name
