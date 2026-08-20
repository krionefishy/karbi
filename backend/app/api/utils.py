from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.app.api.schemas import AutomationResponse, AutomationRunResponse
from backend.modules.wb_reviews.application import (
    AUTOMATION_ID as WB_REVIEWS_ID,
)
from backend.modules.wb_reviews.application import (
    DESCRIPTION as WB_REVIEWS_DESCRIPTION,
)
from backend.modules.wb_reviews.application import (
    TITLE as WB_REVIEWS_TITLE,
)
from backend.modules.wb_reviews.application import SyncOverview
from backend.modules.wb_reviews.domain import ReviewSyncRun
from backend.modules.wb_turnover.application import (
    AUTOMATION_ID as WB_TURNOVER_ID,
)
from backend.modules.wb_turnover.application import (
    DESCRIPTION as WB_TURNOVER_DESCRIPTION,
)
from backend.modules.wb_turnover.application import (
    TITLE as WB_TURNOVER_TITLE,
)
from backend.modules.wb_turnover.application import TurnoverOverview
from backend.modules.wb_turnover.infrastructure.postgres import CollectionRunModel
from backend.shared.settings import Settings


def _next_daily(timezone: ZoneInfo, moments: list[tuple[int, int]], now: datetime | None = None) -> datetime:
    """The soonest of several daily times, today or tomorrow."""
    current = (now or datetime.now(timezone)).astimezone(timezone)
    upcoming = []
    for hour, minute in moments:
        scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        upcoming.append(scheduled if scheduled > current else scheduled + timedelta(days=1))
    return min(upcoming)


def next_run_at(settings: Settings, now: datetime | None = None) -> datetime:
    """When the review worker will next create a run, from the schedule it follows."""
    timezone = ZoneInfo(settings.worker.review_sync_timezone)
    return _next_daily(timezone, [(settings.worker.review_sync_hour, settings.worker.review_sync_minute)], now)


def next_turnover_run_at(settings: Settings, now: datetime | None = None) -> datetime:
    """The turnover automation wakes up several times a day; report the nearest one."""
    turnover = settings.turnover
    timezone = ZoneInfo(turnover.timezone)
    moments = [(hour, 0) for hour in turnover.stock_slot_hours]
    moments.append((turnover.orders_hour, turnover.orders_minute))
    moments.append((turnover.calculation_hour, turnover.calculation_minute))
    moments.append((turnover.digest_hour, turnover.digest_minute))
    return _next_daily(timezone, moments, now)


def _duration(started: datetime | None, finished: datetime | None) -> int | None:
    if not started or not finished:
        return None
    return max(0, int((finished - started).total_seconds()))


def run_response(run: ReviewSyncRun) -> AutomationRunResponse:
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
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def collection_run_response(run: CollectionRunModel) -> AutomationRunResponse:
    return AutomationRunResponse(
        id=run.id,
        trigger=run.trigger,
        status=run.status,
        kind=run.kind,
        snapshot_date=run.run_date.isoformat(),
        created_at=run.started_at.isoformat(),
        started_at=run.started_at.isoformat(),
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        total_sellers=run.sellers,
        completed_sellers=max(run.sellers - run.failed_sellers, 0),
        failed_sellers=run.failed_sellers,
        duration_seconds=_duration(run.started_at, run.finished_at),
    )


def automation_catalog(
    reviews: SyncOverview, turnover: TurnoverOverview, settings: Settings
) -> list[AutomationResponse]:
    """The automations we actually run, described by what they actually did."""
    return [
        AutomationResponse(
            id=WB_REVIEWS_ID,
            title=WB_REVIEWS_TITLE,
            description=WB_REVIEWS_DESCRIPTION,
            status=reviews.status,
            seller_count=reviews.seller_count,
            runs_last_24h=reviews.runs_last_24h,
            last_run=run_response(reviews.last_run) if reviews.last_run else None,
            last_success_at=reviews.last_success_at.isoformat() if reviews.last_success_at else None,
            next_run_at=next_run_at(settings).isoformat(),
        ),
        AutomationResponse(
            id=WB_TURNOVER_ID,
            title=WB_TURNOVER_TITLE,
            description=WB_TURNOVER_DESCRIPTION,
            status=turnover.status,
            seller_count=turnover.seller_count,
            runs_last_24h=turnover.runs_last_24h,
            last_run=collection_run_response(turnover.last_run) if turnover.last_run else None,
            last_success_at=turnover.last_success_at.isoformat() if turnover.last_success_at else None,
            next_run_at=next_turnover_run_at(settings).isoformat(),
        ),
    ]
