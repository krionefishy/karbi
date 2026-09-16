from pydantic import BaseModel, Field


class PenaltyRowResponse(BaseModel):
    rrd_id: int
    report_period: str
    barcode: str
    nm_id: int
    title: str
    kind: str
    group: str
    group_title: str
    amount: float
    sticker_id: str
    srid: str
    assembly_id: str
    order_dt: str | None
    trace: str
    warehouse_name: str | None
    warehouse_id: int | None
    supply_id: str | None
    supply_created_at: str | None
    supply_scan_dt: str | None
    destination_office_name: str | None


class GroupTotalResponse(BaseModel):
    group: str
    title: str
    count: int
    amount: float


class WarehouseOptionResponse(BaseModel):
    warehouse_id: int
    name: str


class PenaltiesResponse(BaseModel):
    seller_id: str
    seller_name: str
    date_from: str
    date_to: str
    collected_at: str | None
    collection_error: str | None
    rows: list[PenaltyRowResponse]
    totals: list[GroupTotalResponse]
    warehouses: list[WarehouseOptionResponse]


class LookupRequest(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=2000)


class LookupMissResponse(BaseModel):
    key: str
    reason: str


class LookupResponse(BaseModel):
    rows: list[PenaltyRowResponse]
    missing: list[LookupMissResponse]


class RefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None
