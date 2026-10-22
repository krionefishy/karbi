from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class DocumentsRow:
    nm_id: int
    vendor_code: str
    title: str
    subject_name: str
    state: str
    reason: str | None
    document_number: str | None
    document_start: date | None
    document_end: date | None
    days_left: int | None
    checked_at: datetime | None
    since: datetime | None


@dataclass(frozen=True, slots=True)
class DocumentsSummary:
    total: int = 0
    ok: int = 0
    rejected: int = 0
    missing: int = 0
    expiring: int = 0
    mismatch: int = 0


NOTICE_STALE = "stale"
NOTICE_COLLECTION_ERROR = "collection_error"
NOTICE_PERMANENT_ERROR = "permanent_error"
NOTICE_NO_SUBSCRIBERS = "no_subscribers"
NOTICE_BOT_MISSING = "bot_missing"
LEVEL_DANGER = "danger"
LEVEL_WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Notice:
    """A banner on the automation page, computed on read: the condition gone, the banner gone."""

    kind: str
    level: str
    since: datetime | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentsBoard:
    collected_at: datetime | None
    last_error: str | None
    stale: bool
    summary: DocumentsSummary
    items: tuple[DocumentsRow, ...]
    notices: tuple[Notice, ...] = ()


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    status: str
    requested_at: datetime
    finished_at: datetime | None
    error: str | None

    @property
    def in_progress(self) -> bool:
        return self.status in ("queued", "running")


@dataclass(frozen=True, slots=True)
class CardDocumentsOverview:
    seller_count: int
    last_success_at: datetime | None
    failing: int

    @property
    def status(self) -> str:
        if self.last_success_at is None:
            return "idle"
        if self.failing:
            return "degraded"
        return "active"
