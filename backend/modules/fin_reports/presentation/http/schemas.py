from pydantic import BaseModel


class LineResponse(BaseModel):
    key: str
    title: str
    level: str


class SellerStateResponse(BaseModel):
    seller_id: str
    name: str
    reports: int
    pending_reports: int
    collected_at: str | None
    built_at: str | None
    error: str | None


class PeriodResponse(BaseModel):
    key: str
    label: str
    date_from: str
    date_to: str
    values: dict[str, float]
    by_seller: dict[str, dict[str, float]]
    pending_sellers: list[str]
    open: bool
    uncosted: float


class UncostedArticleResponse(BaseModel):
    seller_id: str
    seller_name: str
    nm_id: int
    vendor_code: str
    revenue: float


class PnlResponse(BaseModel):
    year: int
    granularity: str
    marketplace: str
    lines: list[LineResponse]
    sellers: list[SellerStateResponse]
    periods: list[PeriodResponse]
    total: dict[str, float]
    total_by_seller: dict[str, dict[str, float]]
    uncosted: list[UncostedArticleResponse]


class CostUploadResponse(BaseModel):
    marketplace: str
    added: int
    changed: int
    unchanged: int
    unknown_cabinets: list[str]
    problems: list[str]
    effective_from: str
