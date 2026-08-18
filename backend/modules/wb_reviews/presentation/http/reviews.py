import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.domain import ReviewSyncRun

router = APIRouter(prefix="/wb/reviews", tags=["wb-reviews"])
MOSCOW = ZoneInfo("Europe/Moscow")


class SyncRunResponse(BaseModel):
    id: uuid.UUID
    trigger: str
    snapshot_date: str
    status: str
    total_sellers: int
    completed_sellers: int
    failed_sellers: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    jobs: list["SyncJobResponse"]


class SyncJobResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    seller_name: str
    status: str
    product_count: int
    feedback_count: int
    error: str | None
    started_at: str | None
    finished_at: str | None


class SnapshotResponse(BaseModel):
    date: str
    ratings: dict[int, int]


class ProductHistoryResponse(BaseModel):
    id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    snapshots: list[SnapshotResponse]


class ReviewHistoryResponse(BaseModel):
    seller_id: uuid.UUID
    products: list[ProductHistoryResponse]


def run_response(run: ReviewSyncRun) -> SyncRunResponse:
    return SyncRunResponse(
        id=run.id,
        trigger=run.trigger,
        snapshot_date=run.snapshot_date.isoformat(),
        status=run.status,
        total_sellers=run.total_sellers,
        completed_sellers=run.completed_sellers,
        failed_sellers=run.failed_sellers,
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        jobs=[
            SyncJobResponse(
                id=job.id,
                seller_id=job.seller_id,
                seller_name=job.seller_name,
                status=job.status,
                product_count=job.product_count,
                feedback_count=job.feedback_count,
                error=job.error,
                started_at=job.started_at.isoformat() if job.started_at else None,
                finished_at=job.finished_at.isoformat() if job.finished_at else None,
            )
            for job in run.jobs
        ],
    )


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
    return ReviewHistoryResponse(
        seller_id=history.seller_id,
        products=[
            ProductHistoryResponse(
                id=product.id,
                article=product.article,
                vendor_code=product.vendor_code,
                name=product.name,
                snapshots=[SnapshotResponse(**snapshot) for snapshot in product.snapshots],
            )
            for product in history.products
        ],
    )
