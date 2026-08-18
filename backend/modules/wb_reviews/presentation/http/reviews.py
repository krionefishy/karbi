import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.presentation.http.schemas import ReviewHistoryResponse, SyncRunResponse
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
