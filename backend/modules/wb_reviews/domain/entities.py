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


GROUP_MAIN = "main"
GROUP_MEMBER = "member"
GROUP_SOLO = "solo"


@dataclass(frozen=True, slots=True)
class GroupOverride:
    """Ручная правка группы болванок поверх склеек WB.

    `member` — артикул привязан к `main_article`; `main` — закреплён основным
    (с `main_article`, если до закрепления был привязан вручную: привязка
    сохраняется); `solo` — выведен из группы.
    """

    article: str
    kind: str
    main_article: str | None
    updated_at: datetime
