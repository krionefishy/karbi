import urllib.parse
import uuid
from datetime import date, datetime, timedelta

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_review_chats.application import (
    XLSX_MEDIA_TYPE,
    DialogView,
    ReviewChatsQueryError,
    ReviewChatsService,
    ReviewChatsView,
)
from backend.modules.wb_review_chats.domain import GroupSummary
from backend.modules.wb_review_chats.presentation.http.schemas import (
    BaselineResponse,
    DaySummaryResponse,
    DialogResponse,
    GroupSummaryResponse,
    ReviewChatsResponse,
)

router = APIRouter(prefix="/wb/review-chats", tags=["wb-review-chats"])

DEFAULT_PERIOD_DAYS = 13
DEFAULT_PAGE_SIZE = 100


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def bad_query(error: ReviewChatsQueryError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def summary_response(summary: GroupSummary) -> GroupSummaryResponse:
    return GroupSummaryResponse(
        total=summary.total,
        replied=summary.replied,
        silent=summary.silent,
        pending=summary.pending,
        reply_rate=summary.reply_rate,
        silent_rate=summary.silent_rate,
        pending_rate=summary.pending_rate,
    )


def dialog_response(item: DialogView) -> DialogResponse:
    dialog = item.dialog
    return DialogResponse(
        chat_id=dialog.chat_id,
        prompt_at=dialog.prompt_at.isoformat(),
        nm_id=dialog.nm_id,
        product_name=item.product_name,
        group=dialog.group,
        outcome=dialog.outcome,
        follow_up_at=_iso(dialog.follow_up_at),
        follow_up_text=dialog.follow_up_text,
        follow_up_late=dialog.follow_up_late,
        reply_at=_iso(dialog.reply_at),
        reply_text=dialog.reply_text,
        reply_has_attachments=dialog.reply_has_attachments,
    )


def view_response(view: ReviewChatsView) -> ReviewChatsResponse:
    return ReviewChatsResponse(
        seller_id=str(view.seller_id),
        seller_name=view.seller_name,
        date_from=view.date_from.isoformat(),
        date_to=view.date_to.isoformat(),
        reply_window_hours=view.reply_window_hours,
        launch_at=_iso(view.launch_at),
        followed=summary_response(view.followed),
        early=summary_response(view.early),
        missed=summary_response(view.missed),
        before=summary_response(view.before),
        after_launch=summary_response(view.after_launch),
        baseline=BaselineResponse(
            date_from=view.baseline.date_from.isoformat(),
            date_to=view.baseline.date_to.isoformat(),
            summary=summary_response(view.baseline.summary),
        )
        if view.baseline
        else None,
        days=[
            DaySummaryResponse(
                day=day.day.isoformat(),
                total=summary_response(day.total),
                followed=summary_response(day.followed),
                early=summary_response(day.early),
                missed=summary_response(day.missed),
            )
            for day in view.days
        ],
        dialogs=[dialog_response(item) for item in view.dialogs],
        page=view.page,
        page_size=view.page_size,
        total_dialogs=view.total_dialogs,
        history_from=_iso(view.history_from),
        synced_through=_iso(view.synced_through),
        collection_error=view.collection_error,
    )


def _period(date_from: date | None, date_to: date | None, service: ReviewChatsService) -> tuple[date, date]:
    """Дни отчёта — московские, поэтому и «сегодня» по умолчанию — московское."""
    end = date_to or datetime.now(service.timezone).date()
    start = date_from or end - timedelta(days=DEFAULT_PERIOD_DAYS)
    return start, end


@router.get("/sellers/{seller_id}", response_model=ReviewChatsResponse)
@inject
async def seller_review_chats(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[ReviewChatsService],
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    group: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=500),
) -> ReviewChatsResponse:
    start, end = _period(date_from, date_to, service)
    try:
        view = await service.view(
            seller_id, start, end, group=group or None, outcome=outcome or None, page=page, page_size=page_size
        )
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except ReviewChatsQueryError as error:
        raise bad_query(error) from error
    return view_response(view)


@router.get("/sellers/{seller_id}/export")
@inject
async def export_review_chats(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[ReviewChatsService],
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> Response:
    start, end = _period(date_from, date_to, service)
    try:
        report = await service.export(seller_id, start, end)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except ReviewChatsQueryError as error:
        raise bad_query(error) from error
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{report.ascii_filename}\"; filename*=UTF-8''{encoded}"
        },
    )
