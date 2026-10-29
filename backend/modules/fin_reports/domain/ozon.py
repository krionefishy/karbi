from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.modules.fin_reports.domain.pnl import LEVEL_ITEM, LEVEL_SUBTOTAL, LEVEL_TOTAL, Line, money
from backend.modules.wb_core.domain import OZON_LINE_SALE

ZERO = Decimal("0.00")
KOPECK = Decimal("0.01")

REVENUE_WITH_POINTS = "revenue_with_points"
REVENUE = "revenue"
POINTS = "points"
DIRECT_EXPENSES = "direct_expenses"
COST = "cost"
COMMISSION = "commission"
DELIVERY = "delivery"
RETURNS = "returns"
ADVERTISING = "advertising"
EXTERNAL_MARKETING = "external_marketing"
ADDITIONAL_SERVICES = "additional_services"
COMPENSATIONS = "compensations"
EAEU_SERVICES = "eaeu_services"
GROSS_MARGIN = "gross_margin"

OZON_LINES: tuple[Line, ...] = (
    Line(REVENUE_WITH_POINTS, "Реализация до СПП (с баллами)", LEVEL_TOTAL),
    Line(REVENUE, "Реализация после СПП (без баллов)", LEVEL_TOTAL),
    Line(POINTS, "инф: Баллы Ozon и программы партнёров", "info"),
    Line(DIRECT_EXPENSES, "Прямые расходы", LEVEL_SUBTOTAL),
    Line(COST, "Себестоимость", LEVEL_ITEM),
    Line(COMMISSION, "Комиссия", LEVEL_ITEM),
    Line(DELIVERY, "Обработка и доставка", LEVEL_ITEM),
    Line(RETURNS, "Возвраты и отмены", LEVEL_ITEM),
    Line(ADVERTISING, "Внутренняя реклама", LEVEL_ITEM),
    Line(EXTERNAL_MARKETING, "Внешний маркетинг", LEVEL_ITEM),
    Line(ADDITIONAL_SERVICES, "Дополнительные услуги", LEVEL_ITEM),
    Line(COMPENSATIONS, "Компенсации и прочие начисления", LEVEL_ITEM),
    Line(EAEU_SERVICES, "Услуги ЕАЭС", LEVEL_ITEM),
    Line(GROSS_MARGIN, "Валовая маржа", LEVEL_TOTAL),
)
OZON_LINE_KEYS = tuple(line.key for line in OZON_LINES)
OZON_EXPENSE_KEYS = tuple(line.key for line in OZON_LINES if line.level == LEVEL_ITEM)

ACCRUAL_TYPE_LINES: dict[int, str] = {
    2: DELIVERY,
    12: DELIVERY,
    13: DELIVERY,
    16: DELIVERY,
    17: DELIVERY,
    21: DELIVERY,
    28: DELIVERY,
    29: DELIVERY,
    30: DELIVERY,
    32: DELIVERY,
    42: DELIVERY,
    43: DELIVERY,
    44: DELIVERY,
    56: DELIVERY,
    73: DELIVERY,
    77: DELIVERY,
    97: DELIVERY,
    98: DELIVERY,
    110: DELIVERY,
    111: DELIVERY,
    112: DELIVERY,
    114: DELIVERY,
    120: DELIVERY,
    121: DELIVERY,
    133: DELIVERY,
    134: DELIVERY,
    135: DELIVERY,
    6: RETURNS,
    9: RETURNS,
    40: RETURNS,
    45: RETURNS,
    53: RETURNS,
    59: RETURNS,
    60: RETURNS,
    65: RETURNS,
    78: RETURNS,
    102: RETURNS,
    103: RETURNS,
    113: RETURNS,
    115: RETURNS,
    3: ADVERTISING,
    4: ADVERTISING,
    5: ADVERTISING,
    33: ADVERTISING,
    36: ADVERTISING,
    41: ADVERTISING,
    47: ADVERTISING,
    49: ADVERTISING,
    54: ADVERTISING,
    55: ADVERTISING,
    61: ADVERTISING,
    70: ADVERTISING,
    75: ADVERTISING,
    96: ADVERTISING,
    116: ADVERTISING,
    130: ADVERTISING,
    19: EXTERNAL_MARKETING,
    23: EXTERNAL_MARKETING,
    31: EXTERNAL_MARKETING,
    87: EXTERNAL_MARKETING,
    8: COMPENSATIONS,
    10: COMPENSATIONS,
    11: COMPENSATIONS,
    25: COMPENSATIONS,
    57: COMPENSATIONS,
    72: COMPENSATIONS,
    81: COMPENSATIONS,
    104: COMPENSATIONS,
    129: COMPENSATIONS,
    26: EAEU_SERVICES,
    62: EAEU_SERVICES,
    63: EAEU_SERVICES,
    64: EAEU_SERVICES,
    66: EAEU_SERVICES,
    67: EAEU_SERVICES,
    68: EAEU_SERVICES,
    86: EAEU_SERVICES,
    99: EAEU_SERVICES,
    100: EAEU_SERVICES,
    122: EAEU_SERVICES,
    123: EAEU_SERVICES,
    124: EAEU_SERVICES,
}


def accrual_line(type_id: int) -> str:
    return ACCRUAL_TYPE_LINES.get(type_id, ADDITIONAL_SERVICES)


@dataclass(slots=True)
class OzonStatement:
    """ОПиУ Ozon за период. Та же форма, что у WB: строки, сумма, закрытие производных."""

    values: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(OZON_LINE_KEYS, ZERO))
    uncosted: Decimal = ZERO

    def add(self, other: "OzonStatement") -> None:
        for key in OZON_LINE_KEYS:
            self.values[key] += other.values[key]
        self.uncosted += other.uncosted

    def close(self) -> "OzonStatement":
        values = self.values
        values[DIRECT_EXPENSES] = sum((values[key] for key in OZON_EXPENSE_KEYS), ZERO)
        values[GROSS_MARGIN] = values[REVENUE_WITH_POINTS] + values[DIRECT_EXPENSES]
        return self


@dataclass(frozen=True, slots=True)
class OzonFact:
    """Сложенные строки начислений: день, SKU, вид строки и тип начисления."""

    day: date
    sku: int
    line: str
    type_id: int
    quantity: int
    amount: Decimal
    sale_amount: Decimal
    sale_price: Decimal
    sale_commission: Decimal
    bonus: Decimal
    coinvestment: Decimal


def ozon_statement(
    facts: Iterable[OzonFact],
    cost_of: Callable[[int], Decimal | None],
    *,
    on_uncosted: Callable[[int, Decimal], None] | None = None,
) -> OzonStatement:
    """ОПиУ из сложенных начислений. `cost_of` — себестоимость единицы SKU или `None`."""
    result = OzonStatement()
    values = result.values
    for fact in facts:
        if fact.line == OZON_LINE_SALE:
            sale = money(fact.sale_amount)
            values[REVENUE_WITH_POINTS] += sale
            values[REVENUE] += money(fact.sale_price) + money(fact.coinvestment)
            values[POINTS] += money(fact.bonus) + money(fact.coinvestment)
            values[COMMISSION] += money(fact.sale_commission)
            if fact.quantity and sale:
                unit_cost = cost_of(fact.sku)
                if unit_cost is None:
                    result.uncosted += sale
                    if on_uncosted is not None:
                        on_uncosted(fact.sku, sale)
                else:
                    sign = -1 if sale < 0 else 1
                    values[COST] -= sign * fact.quantity * unit_cost
        else:
            values[accrual_line(fact.type_id)] += money(fact.amount)
    return result.close()


@dataclass(slots=True)
class OzonSkuRow:
    """Строка листа «ЮНИТ Ozon»: деньги SKU за неделю из начислений."""

    sku: int
    # Артикул продавца и название — из карточек Ozon; у строки «(пусто)» пустые.
    vendor_code: str = ""
    name: str = ""
    sales: int = 0
    returns: int = 0
    revenue_with_points: Decimal = ZERO
    revenue: Decimal = ZERO
    points: Decimal = ZERO
    commission: Decimal = ZERO
    delivery: Decimal = ZERO
    returns_amount: Decimal = ZERO
    advertising: Decimal = ZERO
    # Расход кампаний Ozon по этому SKU из Performance API — справочно; по его долям
    # реклама из начислений, которая приходит без SKU, разложена по товарам.
    ads_info: Decimal = ZERO
    additional_services: Decimal = ZERO
    other: Decimal = ZERO
    unit_cost: Decimal | None = None

    @property
    def payout(self) -> Decimal:
        """К перечислению: продажи с баллами минус всё, что Ozon удержал по этому SKU."""
        return (
            self.revenue_with_points
            + self.commission
            + self.delivery
            + self.returns_amount
            + self.advertising
            + self.additional_services
            + self.other
        )

    @property
    def cost_of_sales(self) -> Decimal:
        return (self.unit_cost or ZERO) * (self.sales - self.returns)

    @property
    def operating_profit(self) -> Decimal:
        return self.payout - self.cost_of_sales


OZON_SKU_COLUMNS: tuple[tuple[str, Callable[[OzonSkuRow], object]], ...] = (
    ("SKU", lambda r: str(r.sku) if r.sku else "(пусто)"),
    ("Артикул продавца", lambda r: r.vendor_code),
    ("Название", lambda r: r.name),
    ("Продажи, шт", lambda r: r.sales),
    ("Возвраты, шт", lambda r: r.returns),
    ("Реализация с баллами", lambda r: r.revenue_with_points),
    ("Реализация без баллов", lambda r: r.revenue),
    ("Баллы Ozon и программы партнёров", lambda r: r.points),
    ("Комиссия", lambda r: r.commission),
    ("Обработка и доставка", lambda r: r.delivery),
    ("Возвраты и отмены", lambda r: r.returns_amount),
    ("Внутренняя реклама", lambda r: r.advertising),
    ("инф: Расход рекламы по Performance", lambda r: -r.ads_info),
    ("Дополнительные услуги", lambda r: r.additional_services),
    ("Прочие начисления", lambda r: r.other),
    ("К перечислению", lambda r: r.payout),
    ("Себестоимость единицы", lambda r: r.unit_cost),
    ("Себестоимость реализованного", lambda r: r.cost_of_sales),
    ("Операционная прибыль", lambda r: r.operating_profit),
)


def ozon_sku_rows(
    facts: Iterable[OzonFact],
    cost_of: Callable[[int], Decimal | None],
    *,
    product_of: Callable[[int], tuple[str, str] | None] | None = None,
    ad_spend: Mapping[int, Decimal] | None = None,
) -> list[OzonSkuRow]:
    """Строки по SKU; начисления без SKU (подписки, услуги кабинета) — в строке «(пусто)».

    Реклама в начислениях приходит одной суммой на кабинет. `ad_spend` — расход
    кампаний по SKU за те же дни из Performance API: по его долям рекламные
    начисления без SKU раскладываются по товарам до копейки, остаток округления
    — SKU с наибольшим расходом. Без расхода реклама остаётся в «(пусто)».
    `product_of` — артикул продавца и название по SKU из карточек Ozon.
    """
    rows: dict[int, OzonSkuRow] = {}

    def row_of(sku: int) -> OzonSkuRow:
        row = rows.get(sku)
        if row is None:
            product = product_of(sku) if product_of is not None and sku else None
            row = rows[sku] = OzonSkuRow(
                sku=sku,
                vendor_code=product[0] if product else "",
                name=product[1] if product else "",
                unit_cost=cost_of(sku) if sku else None,
            )
        return row

    blank_ads = ZERO
    for fact in facts:
        row = row_of(fact.sku)
        if fact.line == OZON_LINE_SALE:
            sale = money(fact.sale_amount)
            if sale < 0:
                row.returns += fact.quantity
            else:
                row.sales += fact.quantity
            row.revenue_with_points += sale
            row.revenue += money(fact.sale_price) + money(fact.coinvestment)
            row.points += money(fact.bonus) + money(fact.coinvestment)
            row.commission += money(fact.sale_commission)
            continue
        line = accrual_line(fact.type_id)
        amount = money(fact.amount)
        if line == DELIVERY:
            row.delivery += amount
        elif line == RETURNS:
            row.returns_amount += amount
        elif line in (ADVERTISING, EXTERNAL_MARKETING):
            if fact.sku:
                row.advertising += amount
            else:
                blank_ads += amount
        elif line == ADDITIONAL_SERVICES:
            row.additional_services += amount
        else:
            row.other += amount
    spent = {sku: amount for sku, amount in (ad_spend or {}).items() if sku and amount > 0}
    for sku, amount in spent.items():
        row_of(sku).ads_info = amount
    if blank_ads and spent:
        for sku, part in _split(blank_ads, spent).items():
            row_of(sku).advertising += part
    elif blank_ads:
        row_of(0).advertising += blank_ads
    return sorted(rows.values(), key=lambda row: (row.sku == 0, -row.revenue_with_points, row.sku))


def _split(amount: Decimal, weights: Mapping[int, Decimal]) -> dict[int, Decimal]:
    """Сумма по долям весов с точностью до копейки; остаток округления — наибольшему весу."""
    total = sum(weights.values(), ZERO)
    parts = {sku: (amount * weight / total).quantize(KOPECK) for sku, weight in weights.items()}
    largest = max(weights, key=lambda sku: (weights[sku], -sku))
    parts[largest] += money(amount).quantize(KOPECK) - sum(parts.values(), ZERO)
    return parts
