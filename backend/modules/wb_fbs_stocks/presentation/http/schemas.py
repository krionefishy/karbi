import uuid

from pydantic import BaseModel, Field


class ColumnResponse(BaseModel):
    warehouse_id: int
    name: str


class GroupResponse(BaseModel):
    id: uuid.UUID
    title: str
    # own — свои склады, fulfilment — фулфилмент, district — округ.
    kind: str
    columns: list[ColumnResponse]


class RowResponse(BaseModel):
    barcode: str
    note: str
    article: str
    title: str
    vendor_code: str
    in_catalog: bool
    # Остаток по складу: ключ — id склада строкой (JSON не умеет числовые ключи).
    amounts: dict[str, int]
    # Сумма по группе: ключ — id группы.
    totals: dict[str, int]


class BoardResponse(BaseModel):
    seller_id: uuid.UUID
    seller_name: str
    collected_at: str | None
    collection_error: str | None
    groups: list[GroupResponse]
    rows: list[RowResponse]


class WarehouseSetupResponse(BaseModel):
    warehouse_id: int
    name: str
    delivery_type: int
    is_deleting: bool
    group_id: uuid.UUID | None
    position: int


class SetupResponse(BaseModel):
    seller_id: uuid.UUID
    groups: list[GroupResponse]
    warehouses: list[WarehouseSetupResponse]


class GroupRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    kind: str = Field(default="district", max_length=16)


class GroupOrderRequest(BaseModel):
    group_ids: list[uuid.UUID] = Field(max_length=100)


class GroupColumnsRequest(BaseModel):
    warehouse_ids: list[int] = Field(max_length=500)


class BarcodesRequest(BaseModel):
    """Баркоды списком или одной строкой через перенос, запятую, пробел."""

    barcodes: list[str] = Field(default_factory=list, max_length=5000)
    text: str = Field(default="", max_length=200_000)


class NoteRequest(BaseModel):
    note: str = Field(default="", max_length=2000)


class RefreshResponse(BaseModel):
    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None


class RefreshAllResponse(BaseModel):
    queued: int


class AddedResponse(BaseModel):
    added: int
