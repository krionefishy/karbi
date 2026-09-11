import uuid
from dataclasses import dataclass
from datetime import datetime

from backend.modules.wb_fbs_stocks.domain import GROUP_DISTRICT, GROUP_OWN


@dataclass(frozen=True, slots=True)
class ColumnView:
    warehouse_id: int
    name: str


@dataclass(frozen=True, slots=True)
class GroupView:
    id: uuid.UUID
    title: str
    kind: str
    columns: tuple[ColumnView, ...]


@dataclass(frozen=True, slots=True)
class RowView:
    """Строка таблицы: баркод селлера и его остатки по столбцам.

    `amounts` — по складу; сумма группы считается здесь же, чтобы страница и
    выгрузка показывали одно число, а не каждая складывала по-своему.
    """

    barcode: str
    note: str
    article: str
    title: str
    vendor_code: str
    # Баркода нет в каталоге кабинета: остаток WB по нему всё равно спросили,
    # но подсказать, что это за товар, нечем.
    in_catalog: bool
    amounts: dict[int, int]

    def amount(self, warehouse_id: int) -> int:
        return self.amounts.get(warehouse_id, 0)

    def group_total(self, group: GroupView) -> int:
        return sum(self.amount(column.warehouse_id) for column in group.columns)


@dataclass(frozen=True, slots=True)
class BoardView:
    seller_id: uuid.UUID
    seller_name: str
    collected_at: datetime | None
    collection_error: str | None
    groups: tuple[GroupView, ...]
    rows: tuple[RowView, ...]

    @property
    def own_group(self) -> GroupView | None:
        return next((group for group in self.groups if group.kind == GROUP_OWN), None)

    @property
    def district_groups(self) -> tuple[GroupView, ...]:
        return tuple(group for group in self.groups if group.kind == GROUP_DISTRICT)


@dataclass(frozen=True, slots=True)
class WarehouseSetup:
    """Склад кабинета и где он стоит в таблице (None — не показывается)."""

    warehouse_id: int
    name: str
    delivery_type: int
    is_deleting: bool
    group_id: uuid.UUID | None
    position: int


@dataclass(frozen=True, slots=True)
class SetupView:
    seller_id: uuid.UUID
    groups: tuple[GroupView, ...]
    warehouses: tuple[WarehouseSetup, ...]


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
class BoardOverview:
    seller_count: int
    last_success_at: datetime | None
    failing: int
    # Опрос идёт по кабинетам: следующий сбор — у того, кого собирали раньше всех,
    # а кабинет без единого сбора — уже в очереди.
    earliest_collected_at: datetime | None = None
    uncollected: int = 0

    @property
    def status(self) -> str:
        if self.last_success_at is None:
            return "idle"
        if self.failing:
            return "degraded"
        return "active"
