import uuid
from dataclasses import dataclass
from datetime import datetime

from backend.modules.wb_card_checklist.domain import ITEMS, ITEMS_BY_KEY, ItemState

STOCK_OK = "ok"
# Селлер не подключён к оборачиваемости: остатков взять неоткуда.
STOCK_NOT_CONNECTED = "not_connected"
# Подключён, но свежих снимков остатков нет — сбор оборачиваемости стоит.
STOCK_STALE = "stale"

REVIEWS_OK = "ok"
REVIEWS_NOT_CONNECTED = "not_connected"
# Подключён к отзывам, но ночной прогон ещё ни разу не прошёл.
REVIEWS_NO_SNAPSHOT = "no_snapshot"


@dataclass(frozen=True, slots=True)
class ChecklistRow:
    article: str
    vendor_code: str
    barcode: str
    title: str
    photo_url: str
    subject_name: str
    card_created_at: datetime | None
    stock: int
    items: tuple[ItemState, ...]
    comment: str

    @property
    def done(self) -> int:
        return sum(1 for item in self.items if item.done is True)

    @property
    def total(self) -> int:
        return len(ITEMS)

    @property
    def ready(self) -> bool:
        return self.done == self.total

    @property
    def note(self) -> str:
        """What is missing, in words: the same text on the page and in the xlsx.

        Unknown items are named apart — «нет данных» is not «не хватает».
        """
        missing = [ITEMS_BY_KEY[item.key].title for item in self.items if item.done is False]
        unknown = [ITEMS_BY_KEY[item.key].title for item in self.items if item.done is None]
        parts = []
        if missing:
            parts.append(f"Не хватает: {', '.join(missing)}")
        if unknown:
            parts.append(f"Нет данных: {', '.join(unknown)}")
        return ". ".join(parts) or "Всё выполнено"


@dataclass(frozen=True, slots=True)
class ChecklistView:
    seller_id: uuid.UUID
    seller_name: str
    collected_at: datetime | None
    collection_error: str | None
    stock_state: str
    reviews_state: str
    min_stock: int
    rows: tuple[ChecklistRow, ...]


@dataclass(frozen=True, slots=True)
class RefreshRequest:
    """State of a «обновить сейчас» press, as the interface polls it."""

    status: str
    requested_at: datetime
    finished_at: datetime | None
    error: str | None

    @property
    def in_progress(self) -> bool:
        return self.status in ("queued", "running")


@dataclass(frozen=True, slots=True)
class ChecklistOverview:
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
