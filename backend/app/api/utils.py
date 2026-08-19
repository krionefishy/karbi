from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.api.schemas import AutomationResponse, AutomationRunResponse
from backend.modules.wb_reviews.application import SyncOverview
from backend.modules.wb_reviews.domain import ReviewSyncRun
from backend.shared.settings import Settings

WB_REVIEWS_ID = "wb-reviews"
WB_REVIEWS_TITLE = "Мониторинг отзывов Wildberries"
WB_REVIEWS_DESCRIPTION = "Ежедневные снимки отзывов по всем товарам и селлерам Wildberries."


def next_run_at(settings: Settings, now: datetime | None = None) -> datetime:
    """When the worker will next create a run, from the same schedule it follows."""
    timezone = ZoneInfo(settings.worker.review_sync_timezone)
    moment = (now or datetime.now(timezone)).astimezone(timezone)
    scheduled = moment.replace(
        hour=settings.worker.review_sync_hour,
        minute=settings.worker.review_sync_minute,
        second=0,
        microsecond=0,
    )
    return scheduled if scheduled > moment else scheduled + timedelta(days=1)


def run_response(run: ReviewSyncRun) -> AutomationRunResponse:
    duration = None
    if run.started_at and run.finished_at:
        duration = max(0, int((run.finished_at - run.started_at).total_seconds()))
    return AutomationRunResponse(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        snapshot_date=run.snapshot_date.isoformat(),
        created_at=run.created_at.isoformat(),
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        total_sellers=run.total_sellers,
        completed_sellers=run.completed_sellers,
        failed_sellers=run.failed_sellers,
        duration_seconds=duration,
    )


def automation_catalog(overview: SyncOverview, settings: Settings) -> list[AutomationResponse]:
    """The automations we actually run, described by what they actually did."""
    return [
        AutomationResponse(
            id=WB_REVIEWS_ID,
            title=WB_REVIEWS_TITLE,
            description=WB_REVIEWS_DESCRIPTION,
            status=overview.status,
            seller_count=overview.seller_count,
            runs_last_24h=overview.runs_last_24h,
            last_run=run_response(overview.last_run) if overview.last_run else None,
            last_success_at=overview.last_success_at.isoformat() if overview.last_success_at else None,
            next_run_at=next_run_at(settings).isoformat(),
        )
    ]
