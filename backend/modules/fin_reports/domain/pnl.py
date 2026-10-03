"""ОПиУ кабинета Wildberries из отчёта реализации.

Строки и формулы повторяют отчёт, которым финансист пользовался раньше, и
сверены с ним на живых отчётах до копейки: выручка, к перечислению,
логистика, штрафы, хранение, приёмка и лояльность — итоги WB как есть, а
удержания раскладываются по названию, которое им даёт WB.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from backend.modules.wb_core.domain import SALES_RETURN_DOC_TYPE, SalesReportTotals

ZERO = Decimal("0.00")

# Строки отчёта в порядке показа. Доходы положительные, расходы отрицательные.
REVENUE_BEFORE_SPP = "revenue_before_spp"
PLANNED_COMMISSION = "planned_commission"
REVENUE_AFTER_SPP = "revenue_after_spp"
FOR_PAY = "for_pay"
DIRECT_EXPENSES = "direct_expenses"
COST = "cost"
CREDIT = "credit"
LOGISTICS = "logistics"
COMMISSION = "commission"
PENALTIES = "penalties"
SURCHARGES = "surcharges"
STORAGE = "storage"
ACCEPTANCE = "acceptance"
ADVERTISING = "advertising"
EXTERNAL_MARKETING = "external_marketing"
RATING_ABUSE = "rating_abuse"
REVIEWS = "reviews"
LOYALTY_POINTS = "loyalty_points"
LOYALTY_PROGRAM = "loyalty_program"
PAYOUT_TERM = "payout_term"
OTHER_DEDUCTIONS = "other_deductions"
GROSS_MARGIN = "gross_margin"

# Уровень строки: итог, подытог или статья расходов внутри «Прямых расходов».
LEVEL_TOTAL = "total"
LEVEL_SUBTOTAL = "subtotal"
LEVEL_ITEM = "item"
LEVEL_INFO = "info"


@dataclass(frozen=True, slots=True)
class Line:
    key: str
    title: str
    level: str


LINES: tuple[Line, ...] = (
    Line(REVENUE_BEFORE_SPP, "Реализация (до СПП)", LEVEL_TOTAL),
    Line(PLANNED_COMMISSION, "Плановая комиссия", LEVEL_TOTAL),
    Line(REVENUE_AFTER_SPP, "Реализация (после СПП)", LEVEL_TOTAL),
    Line(FOR_PAY, "К перечислению за товар", LEVEL_TOTAL),
    Line(DIRECT_EXPENSES, "Прямые расходы", LEVEL_SUBTOTAL),
    Line(COST, "Себестоимость", LEVEL_ITEM),
    Line(CREDIT, "инф: Кредит", LEVEL_INFO),
    Line(LOGISTICS, "Логистика", LEVEL_ITEM),
    Line(COMMISSION, "Комиссия", LEVEL_ITEM),
    Line(PENALTIES, "Штрафы", LEVEL_ITEM),
    Line(SURCHARGES, "Доплаты", LEVEL_ITEM),
    Line(STORAGE, "Хранение", LEVEL_ITEM),
    Line(ACCEPTANCE, "Платная приемка", LEVEL_ITEM),
    Line(ADVERTISING, "Внутренняя реклама", LEVEL_ITEM),
    Line(EXTERNAL_MARKETING, "Внешний маркетинг", LEVEL_ITEM),
    Line(RATING_ABUSE, "ИМИЗР", LEVEL_ITEM),
    Line(REVIEWS, "Отзывы", LEVEL_ITEM),
    Line(LOYALTY_POINTS, "Удержанная за начисленные баллы по лояльности", LEVEL_ITEM),
    Line(LOYALTY_PROGRAM, "Участие в программе лояльности", LEVEL_ITEM),
    Line(PAYOUT_TERM, "Разовое изменение срока перечисления ДС", LEVEL_ITEM),
    Line(OTHER_DEDUCTIONS, "Прочие удержания", LEVEL_ITEM),
    Line(GROSS_MARGIN, "Валовая маржа", LEVEL_TOTAL),
)
LINE_KEYS = tuple(line.key for line in LINES)
# Что складывается в «Прямые расходы». Кредит — справочная строка: погашение
# займа не расход периода, и в маржу оно не входит.
EXPENSE_KEYS = tuple(line.key for line in LINES if line.level == LEVEL_ITEM)

# Удержания WB различаются только названием; к нему дописан номер документа.
# Сверяется нижний регистр, порядок важен: первое совпадение побеждает.
DEDUCTION_KINDS: tuple[tuple[str, str], ...] = (
    ("wb продвижение", ADVERTISING),
    ("баллы за отзывы", REVIEWS),
    ("завышения рейтинга", RATING_ABUSE),
    ("срока перечисления", PAYOUT_TERM),
    ("кредит", CREDIT),
    ("займ", CREDIT),
)


def deduction_kind(bonus_type_name: str) -> str:
    """К какой строке отчёта относится удержание с таким названием."""
    name = bonus_type_name.lower()
    for needle, key in DEDUCTION_KINDS:
        if needle in name:
            return key
    return OTHER_DEDUCTIONS


@dataclass(slots=True)
class Statement:
    """ОПиУ за период по одному кабинету или их сумме.

    `uncosted` — выручка до СПП по артикулам без себестоимости: пока она не
    нулевая, «Себестоимость» и маржа занижены по модулю, и это видно.
    """

    values: dict[str, Decimal] = field(default_factory=lambda: dict.fromkeys(LINE_KEYS, ZERO))
    uncosted: Decimal = ZERO

    def add(self, other: "Statement") -> None:
        for key in LINE_KEYS:
            self.values[key] += other.values[key]
        self.uncosted += other.uncosted

    def close(self) -> "Statement":
        """Производные строки — из первичных: комиссии, прямые расходы, маржа."""
        values = self.values
        values[PLANNED_COMMISSION] = values[FOR_PAY] - values[REVENUE_BEFORE_SPP]
        values[COMMISSION] = values[FOR_PAY] - values[REVENUE_AFTER_SPP]
        values[DIRECT_EXPENSES] = sum((values[key] for key in EXPENSE_KEYS), ZERO)
        values[GROSS_MARGIN] = values[REVENUE_AFTER_SPP] + values[DIRECT_EXPENSES]
        return self


def money(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def statement(
    totals: Iterable[SalesReportTotals],
    cost_of: Callable[[int], Decimal | None],
    *,
    on_uncosted: Callable[[SalesReportTotals, Decimal], None] | None = None,
) -> Statement:
    """ОПиУ из сложенных строк отчёта.

    `cost_of` — себестоимость единицы артикула или `None`; о продажах без
    себестоимости сообщается в `on_uncosted` вместе с их выручкой до СПП.
    """
    result = Statement()
    values = result.values
    for item in totals:
        # Возврат WB отдаёт положительной строкой, а в итогах вычитает.
        sign = -1 if item.doc_type_name == SALES_RETURN_DOC_TYPE else 1
        gross = sign * money(item.gross)
        values[REVENUE_BEFORE_SPP] += gross
        values[REVENUE_AFTER_SPP] += sign * money(item.retail_amount)
        values[FOR_PAY] += sign * money(item.for_pay)
        if item.quantity and gross:
            unit_cost = cost_of(item.nm_id)
            if unit_cost is None:
                result.uncosted += gross
                if on_uncosted is not None:
                    on_uncosted(item, gross)
            else:
                values[COST] -= sign * item.quantity * unit_cost
        values[LOGISTICS] -= money(item.delivery_service)
        values[PENALTIES] -= money(item.penalty)
        values[SURCHARGES] += money(item.additional_payment)
        values[STORAGE] -= money(item.paid_storage)
        values[ACCEPTANCE] -= money(item.paid_acceptance)
        # Лояльность у возврата WB тоже отдаёт положительной и в итогах вычитает — как выручку.
        values[LOYALTY_POINTS] -= sign * money(item.cashback_amount)
        values[LOYALTY_PROGRAM] -= sign * money(item.cashback_commission_change)
        if item.deduction:
            values[deduction_kind(item.bonus_type_name)] -= money(item.deduction)
    return result.close()
