from backend.modules.wb_reviews.application import ProductHistory, ReviewHistory
from backend.modules.wb_reviews.domain import ReviewSyncRun
from backend.modules.wb_reviews.presentation.http.schemas import (
    ProductHistoryResponse,
    ReviewHistoryResponse,
    SnapshotResponse,
    SyncJobResponse,
    SyncRunResponse,
)


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
                attempts=job.attempts,
            )
            for job in run.jobs
        ],
    )


def product_response(product: ProductHistory) -> ProductHistoryResponse:
    return ProductHistoryResponse(
        id=product.id,
        article=product.article,
        vendor_code=product.vendor_code,
        name=product.name,
        imt_id=product.imt_id,
        brand=product.brand,
        photo_url=product.photo_url,
        state=product.state,
        snapshots=[SnapshotResponse(**snapshot) for snapshot in product.snapshots],
        card_snapshots=[SnapshotResponse(**snapshot) for snapshot in product.card_snapshots],
    )


def history_response(history: ReviewHistory) -> ReviewHistoryResponse:
    return ReviewHistoryResponse(
        seller_id=history.seller_id,
        products=[product_response(product) for product in history.products],
    )
