import uuid

from pydantic import BaseModel


class ModeRequest(BaseModel):
    write_enabled: bool


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
