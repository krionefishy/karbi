"""Лист «Остатки»: остаток артикула на конец недели, его себестоимость, цена и оборачиваемость."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from backend.modules.fin_reports.domain.articles import ArticleRow

ZERO = Decimal("0")
DAYS_IN_WEEK = 7
NO_SALES = "Нет продаж!"
# Границы категорий оборачиваемости в днях — как в старом листе.
TURNOVER_BUCKETS: tuple[tuple[int, str], ...] = ((30, "0-30"), (60, "30-60"), (90, "60-90"), (120, "90-120"))
TURNOVER_BEYOND = "более 120"


@dataclass(frozen=True, slots=True)
class StockRow:
    nm_id: int
    vendor_code: str
    tech_size: str
    quantity: int
    unit_cost: Decimal
    # Цена единицы до СПП — средний чек недели; без продаж на неделе — последний известный.
    unit_price: Decimal
    # Реализация до СПП за неделю, в день.
    daily_sales: Decimal

    @property
    def stock_cost(self) -> Decimal:
        return self.unit_cost * self.quantity

    @property
    def stock_price(self) -> Decimal:
        return self.unit_price * self.quantity

    @property
    def turnover_days(self) -> Decimal | None:
        """Дней на распродажу остатка по текущему темпу; `None` — продаж нет."""
        if self.daily_sales <= 0:
            return None
        return self.stock_price / self.daily_sales

    @property
    def category(self) -> str:
        days = self.turnover_days
        if days is None:
            return NO_SALES
        for limit, label in TURNOVER_BUCKETS:
            if days < limit:
                return label
        return TURNOVER_BEYOND


def stock_rows(articles: Iterable[ArticleRow], *, last_price_of) -> list[StockRow]:
    """Строки листа из строк «По артикулам»: только артикулы с остатком на складах WB."""
    rows = []
    for row in articles:
        if not row.nm_id or row.stock_in_warehouse <= 0:
            continue
        price = row.average_gross if row.sales else (last_price_of(row.nm_id) or ZERO)
        rows.append(
            StockRow(
                nm_id=row.nm_id,
                vendor_code=row.vendor_code,
                tech_size=row.tech_size,
                quantity=row.stock_in_warehouse,
                unit_cost=row.cost,
                unit_price=price,
                daily_sales=row.revenue_gross / DAYS_IN_WEEK,
            )
        )
    return sorted(rows, key=lambda item: (item.quantity, item.nm_id))
