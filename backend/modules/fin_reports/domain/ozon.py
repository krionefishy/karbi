"""ОПиУ Ozon из начислений по дням.

Строки — как в листе «ОПиУ_август Озон» старого отчёта. Начисление Ozon
несёт тип из справочника начислений; статья отчёта — по типу. Выручка и
вознаграждение — поля продажи. Проверено на неделе 21–27.09 Шелестюковой:
продажи с баллами, выручка, баллы, программы партнёров, вознаграждение,
продвижение и доставка сошлись с «Экономикой магазина» кабинета до рубля.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.modules.fin_reports.domain.pnl import LEVEL_ITEM, LEVEL_SUBTOTAL, LEVEL_TOTAL, Line, money
from backend.modules.wb_core.domain import OZON_LINE_SALE

ZERO = Decimal("0.00")

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

# Тип начисления Ozon -> статья. Что не названо, идёт в «Дополнительные услуги».
# Номера — из справочника /v1/finance/accrual/types; имена рядом для чтения.
ACCRUAL_TYPE_LINES: dict[int, str] = {
    # Логистика и обработка отправлений.
    2: DELIVERY,  # BackwardShipment
    12: DELIVERY,  # CrossDock
    13: DELIVERY,  # CrossDockPickUpCourierDelivery
    16: DELIVERY,  # Drop-Off
    17: DELIVERY,  # Drop-Off Agent
    21: DELIVERY,  # Fulfillment
    28: DELIVERY,  # LastMile
    29: DELIVERY,  # LastMileCourier
    30: DELIVERY,  # LastMilePickUpPoint
    32: DELIVERY,  # Logistic
    42: DELIVERY,  # Pick-Up
    43: DELIVERY,  # PickUpCourierArrangement
    44: DELIVERY,  # PickUpCourierDelivery
    56: DELIVERY,  # QuantProcessingDrop
    73: DELIVERY,  # Shipment
    77: DELIVERY,  # SupplyInbound
    97: DELIVERY,  # PackageUnitProcessing
    98: DELIVERY,  # DeliveryToHandoverPlaceByOzon
    110: DELIVERY,
    111: DELIVERY,
    112: DELIVERY,
    114: DELIVERY,
    120: DELIVERY,
    121: DELIVERY,
    133: DELIVERY,
    134: DELIVERY,
    135: DELIVERY,
    # Возвраты, отмены и невыкупы.
    6: RETURNS,  # Cancellation
    9: RETURNS,  # ClientReturn
    40: RETURNS,  # PartialReturn
    45: RETURNS,  # PickUpPointReturnAcceptance
    53: RETURNS,  # PreparingToReturn
    59: RETURNS,  # ReturnFlowLogistic
    60: RETURNS,  # ReturnStorageInTheWarehouse
    65: RETURNS,  # RfbsEasyReturn
    78: RETURNS,  # TemporaryPlacement
    102: RETURNS,
    103: RETURNS,
    113: RETURNS,
    115: RETURNS,
    # Продвижение внутри Ozon.
    3: ADVERTISING,  # BrandCommission
    4: ADVERTISING,  # BrandPromotion
    5: ADVERTISING,  # BrandShelf
    33: ADVERTISING,  # Marketing
    36: ADVERTISING,  # OrdersBooking
    41: ADVERTISING,  # PayPerClick
    47: ADVERTISING,  # PointsForReviews
    49: ADVERTISING,  # PremiumCashbackPromotion
    54: ADVERTISING,  # Promotion
    55: ADVERTISING,  # PushCampaign
    61: ADVERTISING,  # ReviewsPin
    70: ADVERTISING,  # SaleReview
    75: ADVERTISING,  # Stencil
    96: ADVERTISING,  # AcceleratedReviewCollection
    116: ADVERTISING,  # FirstCustomerReview
    130: ADVERTISING,  # DisplayAdvertisingPlacement
    # Внешнее продвижение.
    19: EXTERNAL_MARKETING,  # ExternalPromotion
    23: EXTERNAL_MARKETING,  # InternetSiteAdvertising
    31: EXTERNAL_MARKETING,  # LeadGeneration
    87: EXTERNAL_MARKETING,  # SocialMediaAdvertising
    # Компенсации, претензии и корректировки взаиморасчётов.
    8: COMPENSATIONS,  # ClaimCommission
    10: COMPENSATIONS,  # Compensation
    11: COMPENSATIONS,  # CorrectionCommission
    25: COMPENSATIONS,  # ItemCompensation
    57: COMPENSATIONS,  # RealizationReportCorrection
    72: COMPENSATIONS,  # SetOff
    81: COMPENSATIONS,  # VolumeObligationReward
    104: COMPENSATIONS,  # B2CInsuranceCompensation
    129: COMPENSATIONS,  # AnalyticsCorrection
    # Трансграничные продажи и ЕАЭС.
    26: EAEU_SERVICES,  # KazakhstanBuyerInstallment
    62: EAEU_SERVICES,
    63: EAEU_SERVICES,
    64: EAEU_SERVICES,
    66: EAEU_SERVICES,
    67: EAEU_SERVICES,
    68: EAEU_SERVICES,
    86: EAEU_SERVICES,
    99: EAEU_SERVICES,  # InternationalLogisticDelta
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
        # Вознаграждение Ozon берётся с цены с баллами — маржа тоже считается от неё.
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
                    # Возврат приходит отрицательной продажей: себестоимость возвращается тем же знаком.
                    sign = -1 if sale < 0 else 1
                    values[COST] -= sign * fact.quantity * unit_cost
        else:
            values[accrual_line(fact.type_id)] += money(fact.amount)
    return result.close()


@dataclass(slots=True)
class OzonSkuRow:
    """Строка листа «ЮНИТ Ozon»: деньги SKU за неделю из начислений."""

    sku: int
    sales: int = 0
    returns: int = 0
    revenue_with_points: Decimal = ZERO
    revenue: Decimal = ZERO
    points: Decimal = ZERO
    commission: Decimal = ZERO
    delivery: Decimal = ZERO
    returns_amount: Decimal = ZERO
    advertising: Decimal = ZERO
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
    ("Продажи, шт", lambda r: r.sales),
    ("Возвраты, шт", lambda r: r.returns),
    ("Реализация с баллами", lambda r: r.revenue_with_points),
    ("Реализация без баллов", lambda r: r.revenue),
    ("Баллы Ozon и программы партнёров", lambda r: r.points),
    ("Комиссия", lambda r: r.commission),
    ("Обработка и доставка", lambda r: r.delivery),
    ("Возвраты и отмены", lambda r: r.returns_amount),
    ("Внутренняя реклама", lambda r: r.advertising),
    ("Дополнительные услуги", lambda r: r.additional_services),
    ("Прочие начисления", lambda r: r.other),
    ("К перечислению", lambda r: r.payout),
    ("Себестоимость единицы", lambda r: r.unit_cost),
    ("Себестоимость реализованного", lambda r: r.cost_of_sales),
    ("Операционная прибыль", lambda r: r.operating_profit),
)


def ozon_sku_rows(facts: Iterable[OzonFact], cost_of: Callable[[int], Decimal | None]) -> list[OzonSkuRow]:
    """Строки по SKU; начисления без SKU (реклама, подписки) — в строке «(пусто)»."""
    rows: dict[int, OzonSkuRow] = {}
    for fact in facts:
        row = rows.get(fact.sku)
        if row is None:
            row = rows[fact.sku] = OzonSkuRow(sku=fact.sku, unit_cost=cost_of(fact.sku) if fact.sku else None)
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
            row.advertising += amount
        elif line == ADDITIONAL_SERVICES:
            row.additional_services += amount
        else:
            row.other += amount
    return sorted(rows.values(), key=lambda row: (row.sku == 0, -row.revenue_with_points, row.sku))
