from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

CardDocumentState = Literal["rejected", "missing", "expiring", "mismatch", "ok"]


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


class CardDocumentsBoardResponse(BaseModel):
    collected_at: datetime | None
    last_error: str | None
    stale: bool
    summary: CardDocumentsSummaryResponse
    items: list[CardDocumentItemResponse]


class CardDocumentsRefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None


class CardDocumentsInviteLinkResponse(BaseModel):
    url: str
    expires_at: str
