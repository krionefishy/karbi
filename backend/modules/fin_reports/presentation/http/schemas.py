from pydantic import BaseModel


class LineResponse(BaseModel):
    key: str
    title: str
    # total — итог, subtotal — подытог, item — статья расходов, info — справочная строка.
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
    # Строка отчёта -> сумма по выбранным кабинетам.
    values: dict[str, float]
    # Кабинет -> строка отчёта -> сумма.
    by_seller: dict[str, dict[str, float]]
    # Кабинеты, у которых отчёт WB (день начислений Ozon) за период ещё не дочитан: цифры неполные.
    pending_sellers: list[str]
    # Период ещё идёт (его последний день не раньше сегодняшнего): цифры не итоговые.
    open: bool
    # Выручка до СПП по артикулам без себестоимости.
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
    # wb — отчёты реализации WB, ozon — начисления Ozon по дням; набор строк у каждого свой.
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
