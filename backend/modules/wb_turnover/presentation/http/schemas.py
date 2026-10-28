import uuid

from pydantic import BaseModel, Field


class ArticleTurnoverResponse(BaseModel):
    article: str
    name: str
    photo_url: str
    stock_total: int
    stock_fbo: int
    stock_fbs: int
    avg_stock: float
    orders_count: int
    avg_daily_orders: float
    days_of_cover: int | None
    turnover_days: int | None
    stock_days: int
    status: str


class TurnoverArticlesResponse(BaseModel):
    seller_id: uuid.UUID
    date: str | None
    threshold_days: int
    window_days: int
    articles: list[ArticleTurnoverResponse]


class InviteLinkResponse(BaseModel):
    url: str
    expires_at: str


class RefreshResponse(BaseModel):
    """State of the «обновить данные» request the interface polls."""

    status: str
    in_progress: bool
    requested_at: str
    finished_at: str | None
    error: str | None


class BoardSellerResponse(BaseModel):
    seller_id: uuid.UUID
    name: str
    stock_at: str | None
    stock_stale: bool
    fbs_at: str | None
    orders_without_barcode: int


class BoardRowResponse(BaseModel):
    seller_id: uuid.UUID
    seller_name: str
    barcode: str
    article: str
    vendor_code: str
    name: str
    nomenclature: str
    note: str
    in_catalog: bool
    stock: int | None
    orders: int
    rate: float
    days: int | None
    status: str
    tone: str
    fbs: str


class BoardHiddenRowResponse(BaseModel):
    seller_id: uuid.UUID
    seller_name: str
    barcode: str
    note: str
    name: str
    hidden_at: str


class BoardResponse(BaseModel):
    date: str
    window_from: str
    window_to: str
    window_days: int
    red_days: int
    yellow_days: int
    sellers: list[BoardSellerResponse]
    rows: list[BoardRowResponse]
    hidden: list[BoardHiddenRowResponse]


class BoardAddRequest(BaseModel):
    barcodes: list[str] = Field(default_factory=list)
    text: str = ""
    nomenclature: dict[str, str] = Field(default_factory=dict)


class BoardAddedResponse(BaseModel):
    added: int
    existing: int


class BoardHiddenRequest(BaseModel):
    hidden: bool


class BoardItemRequest(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    nomenclature: str | None = Field(default=None, max_length=500)


class BoardRefreshResponse(BaseModel):
    queued: int


class BoardImportCabinetResponse(BaseModel):
    name: str
    seller_name: str | None
    connected_now: bool
    added: int
    existing: int
    unknown: int


class BoardImportResponse(BaseModel):
    cabinets: list[BoardImportCabinetResponse]
