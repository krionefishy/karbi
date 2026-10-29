import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from backend.modules.fin_reports.domain import ArticleRow, OzonSkuRow, Period, Statement, StockRow


class Figures(Protocol):
    """ОПиУ одного периода — WB или Ozon: строки, сумма с другим и закрытие производных."""

    values: dict[str, Decimal]
    uncosted: Decimal

    def add(self, other: Any) -> None: ...

    def close(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class FinReportsOverview:
    """Карточка в каталоге: сколько кабинетов и жив ли сбор отчётов реализации в зеркале."""

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


@dataclass(frozen=True, slots=True)
class SellerState:
    """Кабинет в отчёте и состояние его зеркала.

    `pending_reports` — отчёты года, строки которых зеркало ещё не дочитало:
    их денег в цифрах нет, и отчёт обязан это сказать. У Ozon вместо отчётов
    считаются дни начислений.
    """

    seller_id: uuid.UUID
    name: str
    reports: int
    pending_reports: int
    collected_at: datetime | None
    built_at: datetime | None
    error: str | None


@dataclass(slots=True)
class PeriodColumn:
    """Период отчёта: сумма по выбранным кабинетам и разбивка по каждому."""

    period: Period
    date_from: date
    date_to: date
    total: Figures = field(default_factory=Statement)
    by_seller: dict[uuid.UUID, Figures] = field(default_factory=dict)
    pending: set[uuid.UUID] = field(default_factory=set)
    open: bool = False

    @property
    def label(self) -> str:
        return self.period.label(self.date_from, self.date_to)


@dataclass(frozen=True, slots=True)
class UncostedArticle:
    """Артикул, который продавался в году, а себестоимости у него нет."""

    seller_id: uuid.UUID
    seller_name: str
    nm_id: int
    vendor_code: str
    revenue: Decimal


@dataclass(frozen=True, slots=True)
class PnlView:
    year: int
    granularity: str
    marketplace: str
    sellers: list[SellerState]
    periods: list[PeriodColumn]
    total: Figures
    total_by_seller: dict[uuid.UUID, Figures]
    uncosted: list[UncostedArticle]


@dataclass(frozen=True, slots=True)
class CostUploadResult:
    marketplace: str
    added: int
    changed: int
    unchanged: int
    unknown_cabinets: list[str]
    problems: list[str]
    effective_from: date


@dataclass(frozen=True, slots=True)
class SellerArticles:
    """Листы «По артикулам» и «Остатки» одного кабинета за неделю."""

    seller_id: uuid.UUID
    name: str
    rows: list[ArticleRow]
    stocks: list[StockRow]
    pending: bool
    tax_rate: Decimal | None
    ads_through: date | None


@dataclass(frozen=True, slots=True)
class ArticlesView:
    period: Period
    date_from: date
    date_to: date
    sellers: list[SellerArticles]
    stock_taken_at: datetime | None
    stock_is_live: bool


@dataclass(frozen=True, slots=True)
class SellerSkus:
    """Лист «ЮНИТ Ozon» одного кабинета за неделю."""

    seller_id: uuid.UUID
    name: str
    rows: list[OzonSkuRow]
    pending: bool
    collected_through: date | None
    # Докуда зеркало рекламы Ozon прочитало расход; `None` — не читало (нет ключа Performance).
    ads_through: date | None = None
    ads_error: str | None = None


@dataclass(frozen=True, slots=True)
class SkusView:
    period: Period
    date_from: date
    date_to: date
    sellers: list[SellerSkus]
