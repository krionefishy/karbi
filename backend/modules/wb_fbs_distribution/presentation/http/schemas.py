import uuid

from pydantic import BaseModel, Field


class ModeRequest(BaseModel):
    write_enabled: bool


class RegionResponse(BaseModel):
    code: str
    title: str
    position: int
    """Доля в сотых долях процента: 4000 — это 40%."""
    share_bp: int


class RegionOrderItem(BaseModel):
    code: str
    share_bp: int = Field(ge=0, le=10_000)


class RegionOrderRequest(BaseModel):
    """Порядок направлений задаётся порядком элементов списка."""

    regions: list[RegionOrderItem] = Field(min_length=1)


class OfficeRegionRequest(BaseModel):
    region_code: str | None = None


class SettingsRequest(BaseModel):
    reserve_units: int = Field(ge=0)
    priority_regions: int = Field(ge=1)


class OfficeResponse(BaseModel):
    office_id: int
    name: str
    city: str
    address: str
    federal_district: str
    cargo_type: int
    region_code: str | None
    used_by_cabinets: int


class SetupResponse(BaseModel):
    regions: list[RegionResponse]
    shares_ready: bool
    reserve_units: int
    priority_regions: int
    offices: list[OfficeResponse]
    unassigned_offices: int


class PlacementRequest(BaseModel):
    participates: bool
    position: int = Field(ge=0)


class QueueEntryResponse(BaseModel):
    place: int
    warehouse_id: int
    name: str
    city: str
    region_code: str | None
    region_title: str


class WarehouseResponse(BaseModel):
    warehouse_id: int
    office_id: int
    name: str
    city: str
    address: str
    federal_district: str
    cargo_type: int
    is_processing: bool
    is_deleting: bool
    participates: bool
    position: int
    region_code: str | None


class DistributionOverviewResponse(BaseModel):
    seller_id: uuid.UUID
    mode: str
    write_enabled: bool
    enrolled_at: str
    warehouses_synced_at: str | None
    offices_known: int
    warehouses: list[WarehouseResponse]


class MirrorSyncResponse(BaseModel):
    offices: int
    warehouses: int


class SnapshotStateResponse(BaseModel):
    """Состояние остатка 1С: чем именно сейчас можно считать."""

    snapshot_id: uuid.UUID | None
    source: str
    generated_at: str | None
    received_at: str | None
    lines: int
    stale: bool
    pools: int
    on_hand_total: int
    available_total: int
    reserve_units: int


class SnapshotHistoryItem(BaseModel):
    id: uuid.UUID
    source: str
    generated_at: str
    received_at: str
    lines: int
    status: str
    error: str | None


class PoolResponse(BaseModel):
    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int
    available: int


class MatchResponse(BaseModel):
    matched: int
    catalog_sizes: int
    pools: int


class UnmappedPoolResponse(BaseModel):
    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int


class SharedPoolResponse(BaseModel):
    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int
    sellers: list[uuid.UUID]
    shares: dict[uuid.UUID, int]
    rule_ready: bool


class MappingStateResponse(BaseModel):
    pools: int
    mapped_pools: int
    shared_without_rule: int
    unmapped: list[UnmappedPoolResponse]
    shared: list[SharedPoolResponse]


class PoolShareRequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=128)
    characteristic: str = Field(default="", max_length=255)
    """Пусто — правило снимается, и пул снова считается неразделённым."""
    shares: dict[uuid.UUID, int] = Field(default_factory=dict)


class PlanAmountResponse(BaseModel):
    warehouse_id: int
    name: str
    city: str
    region_code: str | None
    amount: int


class PlanItemResponse(BaseModel):
    chrt_id: int
    item_id: str
    characteristic: str
    name: str
    barcode: str
    on_hand: int
    available: int
    units: int
    amounts: list[PlanAmountResponse]


class PlanSkipResponse(BaseModel):
    chrt_id: int
    item_id: str
    characteristic: str
    name: str
    reason: str
    text: str


class PlanResponse(BaseModel):
    """Рассчитанный план. Ничего в Wildberries не отправлено."""

    id: uuid.UUID
    seller_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    created_at: str
    reserve_units: int
    priority_regions: int
    warehouses: int
    units: int
    items: list[PlanItemResponse]
    skips: list[PlanSkipResponse]
