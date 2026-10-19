import uuid
from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.wb_review_chats.domain import Dialog, GroupSummary


@dataclass(frozen=True, slots=True)
class DaySummary:
    """День по дате автосообщения WB: ответ следующим утром относится ко дню обращения.

    `total` — все диалоги дня; остальные группы до запуска рассылки пусты.
    """

    day: date
    total: GroupSummary
    followed: GroupSummary
    early: GroupSummary
    missed: GroupSummary


@dataclass(frozen=True, slots=True)
class Baseline:
    """Как отвечали до запуска рассылки: все диалоги за окно перед первым нашим сообщением."""

    date_from: date
    date_to: date
    summary: GroupSummary


@dataclass(frozen=True, slots=True)
class DialogView:
    dialog: Dialog
    product_name: str


@dataclass(frozen=True, slots=True)
class ReviewChatsView:
    seller_id: uuid.UUID
    seller_name: str
    date_from: date
    date_to: date
    reply_window_hours: int
    launch_at: datetime | None
    followed: GroupSummary
    early: GroupSummary
    missed: GroupSummary
    before: GroupSummary
    after_launch: GroupSummary
    baseline: Baseline | None
    days: tuple[DaySummary, ...]
    dialogs: tuple[DialogView, ...]
    page: int
    page_size: int
    total_dialogs: int
    history_from: datetime | None
    synced_through: datetime | None
    collection_error: str | None


@dataclass(frozen=True, slots=True)
class ReviewChatsOverview:
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
