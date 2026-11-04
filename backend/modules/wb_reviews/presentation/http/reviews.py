import urllib.parse
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_reviews.application import (
    XLSX_MEDIA_TYPE,
    GroupActionError,
    ProductNotFoundError,
    ReviewReportService,
    ReviewSyncService,
)
from backend.modules.wb_reviews.presentation.http.schemas import (
    GroupChangeRequest,
    ReviewHistoryResponse,
    SyncRunResponse,
)
from backend.modules.wb_reviews.presentation.http.utils import history_response, run_response

router = APIRouter(prefix="/wb/reviews", tags=["wb-reviews"])
MOSCOW = ZoneInfo("Europe/Moscow")


@router.post("/sync", response_model=SyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def start_sync(_: CurrentPrincipal, service: FromDishka[ReviewSyncService]) -> SyncRunResponse:
    run = await service.request("manual", datetime.now(MOSCOW).date())
    return run_response(run)


@router.get("/sync/latest", response_model=SyncRunResponse | None)
@inject
async def latest_sync(_: CurrentPrincipal, service: FromDishka[ReviewSyncService]) -> SyncRunResponse | None:
    run = await service.latest()
    return run_response(run) if run else None


@router.get("/sellers/{seller_id}/history", response_model=ReviewHistoryResponse)
@inject
async def seller_history(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[ReviewSyncService],
    days: int = Query(default=90, ge=7, le=365),
) -> ReviewHistoryResponse:
    try:
        history = await service.history(seller_id, days)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден") from error
    return history_response(history)


@router.put("/sellers/{seller_id}/products/{article}/group", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def change_product_group(
    seller_id: uuid.UUID,
    article: str,
    payload: GroupChangeRequest,
    _: CurrentPrincipal,
    service: FromDishka[ReviewSyncService],
) -> Response:
    try:
        await service.change_group(seller_id, article, payload.action, payload.main_article)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден") from error
    except ProductNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Товар не найден у селлера") from error
    except GroupActionError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


MAX_REPORT_DAYS = 366


@router.get("/sellers/{seller_id}/report")
@inject
async def seller_report(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[ReviewReportService],
    date_from: date = Query(...),
    date_to: date = Query(...),
) -> Response:
    if date_from > date_to:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Начало периода позже его конца")
    if (date_to - date_from).days >= MAX_REPORT_DAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Период не может быть длиннее года")
    try:
        report = await service.build(seller_id, date_from, date_to)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден") from error
    fallback = f"reviews_{date_from.isoformat()}_{date_to.isoformat()}.xlsx"
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"},
    )
