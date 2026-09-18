import uuid
from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.wb_review_chats.domain import Dialog, GroupSummary


@dataclass(frozen=True, slots=True)
class DaySummary:
    """День по дате автосообщения WB: ответ следующим утром относится ко дню обращения."""

    day: date
    followed: GroupSummary
    bare: GroupSummary


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
    followed: GroupSummary
    bare: GroupSummary
    # Автосообщения WB, следом за которыми наше не ушло или ушло после ответа покупателя.
    follow_up_late: int
    # Первое наше сообщение в периоде — ориентир, с какого дня сравнение «с» и «без» имеет смысл.
    first_follow_up_at: datetime | None
    days: tuple[DaySummary, ...]
    dialogs: tuple[DialogView, ...]
    page: int
    page_size: int
    total_dialogs: int
    # Состояние зеркала чатов: с какого момента есть история, докуда дочитано, текст ошибки.
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
