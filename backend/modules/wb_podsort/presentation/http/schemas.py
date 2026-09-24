from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    window_days: int
    cover_days: int
    regions: list[str]
    window_choices: list[int]
    max_cover_days: int
    available_regions: list[str]


class SettingsRequest(BaseModel):
    window_days: int
    cover_days: int
    regions: list[str] = Field(default_factory=list)


class SellerStateResponse(BaseModel):
    seller_id: str
    name: str
    window_days_loaded: int
    history_from: str | None
    history_days_loaded: int
    collected_at: str | None
    collection_error: str | None
    remains_at: str | None
    remains_error: str | None


class WarehouseResponse(BaseModel):
    name: str
    region: str | None
    source: str
    quantity: int


class WarehouseRegionRequest(BaseModel):
    name: str
    region: str | None = None
    # Вернуть угадывание по городу в названии вместо ручного выбора.
    guess: bool = False


class SummaryRowResponse(BaseModel):
    seller_name: str
    nm_id: int
    barcode: str
    vendor_code: str
    subject: str
    tech_size: str
    need: int
    window_orders: int
    average: float
    stock: int
    cover_days: float | None


class PodsortResponse(BaseModel):
    today: str
    last_day: str
    window_start: str
    settings: SettingsResponse
    sellers: list[SellerStateResponse]
    warehouses: list[WarehouseResponse]
    region: str
    rows: list[SummaryRowResponse]
