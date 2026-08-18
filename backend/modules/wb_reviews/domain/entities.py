import uuid
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DailyRatings:
    seller_id: uuid.UUID
    article: str
    date: date
    ratings: tuple[int, int, int, int, int]
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewSyncJob:
    id: uuid.UUID
    seller_id: uuid.UUID
    seller_name: str
    status: str
    product_count: int
    feedback_count: int
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class ReviewSyncRun:
    id: uuid.UUID
    trigger: str
    snapshot_date: date
    status: str
    total_sellers: int
    completed_sellers: int
    failed_sellers: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    jobs: tuple[ReviewSyncJob, ...] = ()
