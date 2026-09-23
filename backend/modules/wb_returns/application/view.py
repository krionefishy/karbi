import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.modules.wb_returns.domain import (
    CLAIM_REVIEW_DAYS,
    FREE_STORAGE_DAYS,
    RETURN_STATUS_TITLES,
    STORAGE_DAYS,
    Claim,
    ReturnItem,
)


@dataclass(frozen=True, slots=True)
class ReturnView:
    """Возврат как его показывает страница: строка отчёта плюс сроки хранения."""

    item: ReturnItem
    status_changed_at: datetime
    first_seen_at: datetime

    @property
    def status_title(self) -> str:
        return RETURN_STATUS_TITLES.get(self.item.status_key, self.item.status or "Статус не определён")

    @property
    def ready_at(self) -> datetime | None:
        """От какого момента считать хранение: WB даёт дату готовности, иначе — когда мы её увидели."""
        if self.item.status_key != "ready":
            return None
        return self.item.ready_to_return_dt or self.status_changed_at

    @property
    def free_until(self) -> datetime | None:
        ready = self.ready_at
        return ready + timedelta(days=FREE_STORAGE_DAYS) if ready else None

    @property
    def pickup_deadline(self) -> datetime | None:
        ready = self.ready_at
        return ready + timedelta(days=STORAGE_DAYS) if ready else None


@dataclass(frozen=True, slots=True)
class ClaimView:
    claim: Claim
    first_seen_at: datetime

    @property
    def review_deadline(self) -> datetime:
        return self.claim.dt + timedelta(days=CLAIM_REVIEW_DAYS)


@dataclass(frozen=True, slots=True)
class ReturnsView:
    seller_id: uuid.UUID
    seller_name: str
    collected_at: datetime | None
    collection_error: str | None
    ready: tuple[ReturnView, ...]
    transit: tuple[ReturnView, ...]
    other_active: tuple[ReturnView, ...]
    history: tuple[ReturnView, ...]
    claims: tuple[ClaimView, ...]
    claims_history: tuple[ClaimView, ...]


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
class ReturnsOverview:
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
