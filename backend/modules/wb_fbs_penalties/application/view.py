import uuid
from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.wb_fbs_penalties.domain import GROUP_TITLES, ReportRow

# Как строка отчёта сошлась с заданием из зеркала.
TRACE_FOUND = "found"  # задание и поставка есть
TRACE_NO_SUPPLY = "no_supply"  # задание есть, в поставку не попало
TRACE_NO_ORDER = "no_order"  # задания в зеркале нет


@dataclass(frozen=True, slots=True)
class PenaltyRowView:
    """Строка отчёта с удержанием и её логистика по зеркалу заданий."""

    row: ReportRow
    trace: str
    warehouse_id: int | None
    warehouse_name: str | None
    order_created_at: datetime | None
    supply_id: str | None
    supply_created_at: datetime | None
    supply_scan_dt: datetime | None
    destination_office_name: str | None

    @property
    def group(self) -> str:
        return self.row.group or ""

    @property
    def group_title(self) -> str:
        return GROUP_TITLES.get(self.group, self.group)


@dataclass(frozen=True, slots=True)
class GroupTotal:
    group: str
    title: str
    count: int
    amount: float


@dataclass(frozen=True, slots=True)
class WarehouseOption:
    warehouse_id: int
    name: str


@dataclass(frozen=True, slots=True)
class PenaltiesView:
    seller_id: uuid.UUID
    seller_name: str
    date_from: date
    date_to: date
    collected_at: datetime | None
    collection_error: str | None
    rows: tuple[PenaltyRowView, ...]
    # Итоги по всем группам за период — вкладкам нужны счётчики и тогда, когда
    # открыта одна группа. Фильтр по складу их сужает, страница — нет.
    totals: tuple[GroupTotal, ...]
    warehouses: tuple[WarehouseOption, ...]
    page: int = 1
    page_size: int | None = None
    total_rows: int = 0


@dataclass(frozen=True, slots=True)
class LookupMiss:
    key: str
    reason: str


@dataclass(frozen=True, slots=True)
class LookupView:
    """Ответ на вставленные номера: что нашлось в отчётах, что только среди заданий, что нигде."""

    rows: tuple[PenaltyRowView, ...]
    missing: tuple[LookupMiss, ...]


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
class PenaltiesOverview:
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
