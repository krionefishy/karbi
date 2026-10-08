import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from backend.modules.fin_reports.domain import ArticleRow, Period, Statement, StockRow


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
    их денег в цифрах нет, и отчёт обязан это сказать.
    """

    seller_id: uuid.UUID
    name: str
    reports: int
    pending_reports: int
    collected_at: datetime | None
    # Когда воркер в последний раз складывал отчёты кабинета в суммы; `None` — ещё ни разу.
    built_at: datetime | None
    error: str | None


@dataclass(slots=True)
class PeriodColumn:
    """Период отчёта: сумма по выбранным кабинетам и разбивка по каждому."""

    period: Period
    date_from: date
    date_to: date
    total: Statement = field(default_factory=Statement)
    by_seller: dict[uuid.UUID, Statement] = field(default_factory=dict)
    # Кабинеты, у которых за период есть недочитанный отчёт.
    pending: set[uuid.UUID] = field(default_factory=set)

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
    sellers: list[SellerState]
    # Свежие первыми — как в отчёте, к которому привык финансист.
    periods: list[PeriodColumn]
    total: Statement
    total_by_seller: dict[uuid.UUID, Statement]
    uncosted: list[UncostedArticle]


@dataclass(frozen=True, slots=True)
class CostUploadResult:
    marketplace: str
    # Новые версии: артикул появился впервые или у него сменилась цена.
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
    # Отчёты недели у кабинета есть, но не все дочитаны и сложены.
    pending: bool
    # Ставка налога, по которой считалась колонка «Налог»; `None` — не задана, налог ноль.
    tax_rate: Decimal | None


@dataclass(frozen=True, slots=True)
class ArticlesView:
    period: Period
    date_from: date
    date_to: date
    sellers: list[SellerArticles]
    # Откуда остаток: снимок на конец недели или зеркало на момент выгрузки.
    stock_taken_at: datetime | None
    stock_is_live: bool
