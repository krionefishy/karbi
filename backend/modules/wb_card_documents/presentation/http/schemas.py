from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

CardDocumentState = Literal["rejected", "missing", "expiring", "mismatch", "ok"]
CardDocumentsNoticeKind = Literal["stale", "collection_error", "permanent_error", "no_subscribers", "bot_missing"]
CardDocumentsNoticeLevel = Literal["danger", "warning"]


class CardDocumentsSummaryResponse(BaseModel):
    total: int
    ok: int
    rejected: int
    missing: int
    expiring: int
    mismatch: int


class CardDocumentItemResponse(BaseModel):
    nm_id: int
    vendor_code: str
    title: str
    subject_name: str
    state: CardDocumentState
    reason: str | None
    document_number: str | None
    document_start: date | None
    document_end: date | None
    days_left: int | None
    checked_at: datetime | None
    since: datetime | None


class CardDocumentsNoticeResponse(BaseModel):
    """Баннер страницы. Вычисляется на чтении: пропало условие — пропал баннер."""

    kind: CardDocumentsNoticeKind
    level: CardDocumentsNoticeLevel
    since: datetime | None
    detail: str | None


class CardDocumentsBoardResponse(BaseModel):
    collected_at: datetime | None
    last_error: str | None = Field(
        description="Устарело: используйте notices (collection_error / permanent_error).", deprecated=True
    )
    stale: bool = Field(description="Устарело: используйте notices (stale).", deprecated=True)
    summary: CardDocumentsSummaryResponse
    items: list[CardDocumentItemResponse]
    notices: list[CardDocumentsNoticeResponse]


class CardDocumentsRefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None


class CardDocumentsInviteLinkResponse(BaseModel):
    url: str
    expires_at: str
