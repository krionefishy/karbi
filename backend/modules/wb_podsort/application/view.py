import uuid
from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.wb_podsort.domain import PodsortRow, PodsortSettings


@dataclass(frozen=True, slots=True)
class SellerState:
    """Кабинет в подсорте: докуда догружены заказы и когда сняты остатки WB."""

    seller_id: uuid.UUID
    name: str
    window_days_loaded: int
    history_from: date | None
    history_days_loaded: int
    collected_at: datetime | None
    collection_error: str | None
    remains_at: datetime | None
    remains_stale: bool
    remains_error: str | None


@dataclass(frozen=True, slots=True)
class WarehouseView:
    name: str
    region: str | None
    source: str
    quantity: int


@dataclass(frozen=True, slots=True)
class SummaryRow:
    seller_name: str
    row: PodsortRow
    need: int


@dataclass(frozen=True, slots=True)
class PodsortView:
    today: date
    last_day: date
    window_start: date
    settings: PodsortSettings
    sellers: tuple[SellerState, ...]
    warehouses: tuple[WarehouseView, ...]
    region: str
    rows: tuple[SummaryRow, ...]


@dataclass(frozen=True, slots=True)
class PodsortOverview:
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
