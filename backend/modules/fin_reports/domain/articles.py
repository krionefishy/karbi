"""Лист «По артикулам»: юнит-экономика артикула за неделю из строк отчёта реализации.

Колонки и формулы повторяют отчёт, которым финансист пользовался раньше,
и восстановлены по его цифрам: итог услуг WB — плановая комиссия плюс все
удержания, налог — от реализации после СПП, прибыль — к оплате без налога
минус себестоимость. Проценты от отрицательной прибыли там нули, здесь тоже.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from backend.modules.fin_reports.domain.pnl import (
    ADVERTISING,
    CREDIT,
    OTHER_DEDUCTIONS,
    PAYOUT_TERM,
    RATING_ABUSE,
    REVIEWS,
    deduction_kind,
    money,
)
from backend.modules.wb_core.domain import SALES_RETURN_DOC_TYPE, SalesReportTotals

ZERO = Decimal("0")
SALE_DOC_TYPE = "Продажа"
# Строки без артикула (удержания по документу, а не по товару) собираются в одну.
NO_ARTICLE = 0

# Направление логистики WB пишет в название строки.
TO_CLIENT = "к клиенту"
FROM_CLIENT = "от клиента"
CANCELLED = "при отмене"
OWN_RETURN = "возврат"


@dataclass(frozen=True, slots=True)
class Stock:
    """Остаток артикула на конец недели: на складах WB, в пути до клиента и от клиента."""

    in_warehouse: int = 0
    to_client: int = 0
    from_client: int = 0
    # Всего: на складах плюс в пути туда и обратно.
    total: int = 0


@dataclass(frozen=True, slots=True)
class AdSpend:
    """Реклама артикула за неделю: с баланса, со счёта, бонусами, и справочная цифра из «Продвижения»."""

    balance: Decimal = ZERO
    account: Decimal = ZERO
    bonus: Decimal = ZERO
    promotion_info: Decimal = ZERO

    @property
    def paid(self) -> Decimal:
        return self.balance + self.account


def _ratio(numerator: Decimal, denominator: Decimal | int) -> Decimal:
    return numerator / Decimal(denominator) if denominator else ZERO


def _positive_ratio(numerator: Decimal, denominator: Decimal | int) -> Decimal:
    """Доли прибыли: от убытка в старом отчёте стоял ноль, не отрицательный процент."""
    return _ratio(numerator, denominator) if numerator > 0 else ZERO


@dataclass(slots=True)
class ArticleRow:
    """Одна строка листа — артикул и размер кабинета за неделю. Имена полей — по буквам старого листа."""

    nm_id: int
    vendor_code: str = ""
    tech_size: str = ""
    # Штуки.
    deliveries: int = 0  # E
    refusals: int = 0  # F
    sales: int = 0  # H
    self_buyouts: int = 0  # I — руками, у нас всегда ноль
    giveaways: int = 0  # J — руками
    dbs: int = 0  # K
    returns: int = 0  # N
    stock_from_client: int = 0  # P
    stock_to_client: int = 0  # Q
    stock_in_warehouse: int = 0  # R
    stock_total: int = 0  # S
    sale_corrections: int = 0  # T
    disposed: int = 0  # W
    payout_corrections_qty: int = 0  # X
    # Деньги до СПП.
    sales_gross: Decimal = ZERO  # Z
    returns_gross: Decimal = ZERO  # AA
    corrections_gross: Decimal = ZERO  # AB
    dbs_gross: Decimal = ZERO  # AG
    # Деньги после СПП.
    sales_after: Decimal = ZERO  # AL
    returns_after: Decimal = ZERO  # AM
    corrections_after: Decimal = ZERO  # AN
    # Перечисление и услуги.
    payout_corrections: Decimal = ZERO  # AV
    for_pay: Decimal = ZERO  # AW
    acquiring: Decimal = ZERO  # BD
    logistics: Decimal = ZERO  # BF
    logistics_to_client: Decimal = ZERO  # BG
    logistics_from_client: Decimal = ZERO  # BH
    logistics_own_return: Decimal = ZERO  # BI
    penalties: Decimal = ZERO  # BO
    reward_corrections: Decimal = ZERO  # BR
    storage: Decimal = ZERO  # BU
    acceptance: Decimal = ZERO  # BX
    ads: AdSpend = field(default_factory=AdSpend)  # CA–CE
    rating_abuse: Decimal = ZERO  # CI
    reviews: Decimal = ZERO  # CL
    credit: Decimal = ZERO  # CO
    loyalty_points: Decimal = ZERO  # CR
    loyalty_program: Decimal = ZERO  # CU
    payout_term: Decimal = ZERO  # CX
    other_deductions: Decimal = ZERO  # DA
    damage_payout: Decimal = ZERO  # DB
    tax_rate: Decimal = ZERO  # доля, не проценты
    unit_cost: Decimal | None = None  # EB
    marketing: Decimal = ZERO  # EM — внешний, руками
    # Доли в итогах кабинета, проставляются после расчёта всех строк.
    revenue_share: Decimal = ZERO  # AD
    size_share: Decimal = ZERO  # M
    profit_share: Decimal = ZERO  # ER

    # --- штуки --------------------------------------------------------------------
    @property
    def refusal_rate(self) -> Decimal:  # G
        return _ratio(Decimal(self.refusals), self.deliveries)

    @property
    def organic_sales(self) -> int:  # L
        return self.sales - self.self_buyouts - self.giveaways - self.dbs

    @property
    def return_rate(self) -> Decimal:  # O
        return _ratio(Decimal(self.returns), self.sales)

    @property
    def realized(self) -> int:  # U
        return self.sales - self.returns + self.sale_corrections

    @property
    def buyout_rate(self) -> Decimal:  # V
        return _ratio(Decimal(self.realized), self.deliveries)

    # --- выручка -----------------------------------------------------------------
    @property
    def revenue_gross(self) -> Decimal:  # AC
        return self.sales_gross + self.returns_gross + self.corrections_gross

    @property
    def average_gross(self) -> Decimal:  # AE
        return _ratio(self.sales_gross, self.sales)

    @property
    def planned_commission_rate(self) -> Decimal:  # AF
        return _ratio(self.planned_commission, self.revenue_gross)

    @property
    def average_dbs_gross(self) -> Decimal:  # AH
        return _ratio(self.dbs_gross, self.dbs)

    @property
    def sales_gross_without_dbs(self) -> Decimal:  # AI
        return self.sales_gross - self.dbs_gross

    @property
    def average_gross_without_dbs(self) -> Decimal:  # AJ
        return _ratio(self.sales_gross_without_dbs, self.sales - self.dbs)

    @property
    def revenue_after(self) -> Decimal:  # AO, AQ
        return self.sales_after + self.returns_after + self.corrections_after

    @property
    def average_after(self) -> Decimal:  # AP, AR
        return _ratio(self.sales_after, self.sales)

    @property
    def spp(self) -> Decimal:  # AS
        return self.revenue_gross - self.revenue_after

    @property
    def spp_rate(self) -> Decimal:  # AT
        return _ratio(self.spp, self.revenue_gross)

    # --- комиссии ----------------------------------------------------------------
    @property
    def planned_commission(self) -> Decimal:  # AY
        return self.revenue_gross - self.for_pay

    @property
    def actual_commission(self) -> Decimal:  # BA
        return self.revenue_after - self.for_pay

    @property
    def logistics_other(self) -> Decimal:  # BJ
        return self.logistics - self.logistics_to_client - self.logistics_from_client - self.logistics_own_return

    # --- итоги услуг WB -----------------------------------------------------------
    @property
    def services_gross(self) -> Decimal:  # DE
        return (
            self.planned_commission
            + self.logistics
            + self.penalties
            + self.reward_corrections
            + self.storage
            + self.acceptance
            + self.ads.paid
            + self.rating_abuse
            + self.reviews
            + self.credit
            + self.loyalty_points
            + self.loyalty_program
            + self.payout_term
            + self.other_deductions
        )

    @property
    def services_gross_without_account_ads(self) -> Decimal:  # DF
        return self.services_gross - self.ads.account

    @property
    def services_gross_without_credit(self) -> Decimal:  # DI
        return self.services_gross - self.credit

    @property
    def services_after(self) -> Decimal:  # DL
        return self.services_gross - self.spp

    @property
    def services_after_without_account_ads(self) -> Decimal:  # DM
        return self.services_after - self.ads.account

    @property
    def payout(self) -> Decimal:  # DQ, DS
        return self.revenue_after - self.services_after

    @property
    def tax(self) -> Decimal:  # DU
        # В старом отчёте копейки налога отбрасывались, а не округлялись.
        return (self.revenue_after * self.tax_rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    @property
    def payout_after_tax(self) -> Decimal:  # DX
        return self.payout - self.tax

    # --- себестоимость и прибыль -----------------------------------------------------
    @property
    def cost(self) -> Decimal:  # EB — ноль, когда себестоимости нет
        return self.unit_cost or ZERO

    @property
    def cost_of_sales(self) -> Decimal:  # DY
        return self.cost * self.realized

    @property
    def cost_rate(self) -> Decimal:  # EA
        return _ratio(self.cost_of_sales, self.revenue_gross)

    @property
    def operating_profit(self) -> Decimal:  # EQ
        return self.payout_after_tax - self.cost_of_sales - self.marketing

    def per_unit(self, value: Decimal) -> Decimal:
        return _ratio(value, self.realized)

    def of_gross(self, value: Decimal) -> Decimal:
        return _ratio(value, self.revenue_gross)

    def of_after(self, value: Decimal) -> Decimal:
        return _ratio(value, self.revenue_after)

    @property
    def profit_rate_gross(self) -> Decimal:  # ET
        return _positive_ratio(self.operating_profit, self.revenue_gross)

    @property
    def profit_rate_after(self) -> Decimal:  # EU
        return _positive_ratio(self.operating_profit, self.revenue_after)

    @property
    def profit_rate_cost(self) -> Decimal:  # EV
        return _positive_ratio(self.operating_profit, self.cost_of_sales)


# Колонки листа в порядке старого отчёта: заголовок и как взять значение из строки.
# Служебные колонки-разделители («1», «2»…) старого листа оставлены на своих местах.
ARTICLE_COLUMNS: tuple[tuple[str, Callable[[ArticleRow], object]], ...] = (
    ("Артикул ВБ", lambda r: str(r.nm_id) if r.nm_id else "(пусто)"),
    ("Артикул продавца", lambda r: r.vendor_code),
    ("Размер", lambda r: r.tech_size),
    ("Доставки", lambda r: r.deliveries),
    ("Отказы", lambda r: r.refusals),
    ("% отказов", lambda r: r.refusal_rate),
    ("Продажи", lambda r: r.sales),
    ("Самовыкупы", lambda r: r.self_buyouts),
    ("Раздачи", lambda r: r.giveaways),
    ("DBS", lambda r: r.dbs),
    ("Продажи (органические)", lambda r: r.organic_sales),
    ("Доля размера в продажах", lambda r: r.size_share),
    ("Возвраты", lambda r: r.returns),
    ("% возвратов", lambda r: r.return_rate),
    ("Остатки от клиента на последнюю неделю", lambda r: r.stock_from_client),
    ("Остатки до клиента на последнюю неделю", lambda r: r.stock_to_client),
    ("Остатки на складах  на последнюю неделю", lambda r: r.stock_in_warehouse),
    ("Остатки всего на последнюю неделю", lambda r: r.stock_total),
    ("Корректировки в продажах", lambda r: r.sale_corrections),
    ("Итого кол-во реализованного товара", lambda r: r.realized),
    ("% выкупа", lambda r: r.buyout_rate),
    ("Утилизировано", lambda r: r.disposed),
    ("Корректировки в перечислении за товар шт", lambda r: r.payout_corrections_qty),
    ("1", lambda r: None),
    ("Продажи до СПП", lambda r: r.sales_gross),
    ("Возвраты до СПП", lambda r: r.returns_gross),
    ("Корректировки в продажах до СПП", lambda r: r.corrections_gross),
    ("Вся стоимость реализованного товара до СПП", lambda r: r.revenue_gross),
    ("% от всей суммы реализации", lambda r: r.revenue_share),
    ("Средний чек продажи до СПП", lambda r: r.average_gross),
    ("% комиссии ВБ до СПП", lambda r: r.planned_commission_rate),
    ("Продажи DBS до спп", lambda r: r.dbs_gross),
    ("Средняя цена DBS до СПП", lambda r: r.average_dbs_gross),
    ("Продажи до СПП без DBS", lambda r: r.sales_gross_without_dbs),
    ("Средние продажи до СПП без DBS", lambda r: r.average_gross_without_dbs),
    ("2", lambda r: None),
    ("Продажи после СПП", lambda r: r.sales_after),
    ("Возвраты после СПП", lambda r: r.returns_after),
    ("Корректировки в продажах после СПП", lambda r: r.corrections_after),
    ("Вся стоимость реализованного товара после СПП", lambda r: r.revenue_after),
    ("Средний чек продажи после СПП", lambda r: r.average_after),
    ("Вся стоимость реализованного товара после СПП (органическая)", lambda r: r.revenue_after),
    ("Средний чек продажи после СПП (органический)", lambda r: r.average_after),
    ("Сумма СПП", lambda r: r.spp),
    ("% СПП", lambda r: r.spp_rate),
    ("3", lambda r: None),
    ("Корректировки в перечислении за товар", lambda r: r.payout_corrections),
    ("К перечислению за товар", lambda r: r.for_pay),
    ("3.1", lambda r: None),
    ("Плановая комиссия", lambda r: r.planned_commission),
    ("Плановая комиссия на единицу", lambda r: r.per_unit(r.planned_commission)),
    ("Фактическая комиссия", lambda r: r.actual_commission),
    ("Фактическая комиссия на единицу товара", lambda r: r.per_unit(r.actual_commission)),
    ("% фактической комиссии от реализациии до СПП", lambda r: r.of_gross(r.actual_commission)),
    ("Инф: Эквайринг", lambda r: r.acquiring),
    ("% эквайринга от реализациии до СПП", lambda r: r.of_gross(r.acquiring)),
    ("Стоимость логистики", lambda r: r.logistics),
    ("в т.ч.  Стоимость логистики до клиента", lambda r: r.logistics_to_client),
    ("в т.ч.  Стоимость логистики от клиента", lambda r: r.logistics_from_client),
    ("в т.ч. Логистика возврат своего товара", lambda r: r.logistics_own_return),
    ("в т.ч. Логистика другое", lambda r: r.logistics_other),
    ("Логистика на единицу товара", lambda r: r.per_unit(r.logistics)),
    ("% логистики от реализациии до СПП", lambda r: r.of_gross(r.logistics)),
    ("в т.ч. % логистики до клиента от реализации до СПП", lambda r: r.of_gross(r.logistics_to_client)),
    ("в т.ч. % логистики от клиента от реализации до СПП", lambda r: r.of_gross(r.logistics_from_client)),
    ("Штрафы", lambda r: r.penalties),
    ("Штрафы на единицу товара", lambda r: r.per_unit(r.penalties)),
    ("% штрафа от реализациии до СПП", lambda r: r.of_gross(r.penalties)),
    ("Корректировки вознаграждения", lambda r: r.reward_corrections),
    ("Корректировки вознаграждения на единицу товара", lambda r: r.per_unit(r.reward_corrections)),
    ("% Корректировки вознаграждения от реализациии до СПП", lambda r: r.of_gross(r.reward_corrections)),
    ("Хранение", lambda r: r.storage),
    ("Хранение на единицу товара", lambda r: r.per_unit(r.storage)),
    ("% хранения от реализациии до СПП", lambda r: r.of_gross(r.storage)),
    ("Платная приемка", lambda r: r.acceptance),
    ("Платная приемка на единицу товара", lambda r: r.per_unit(r.acceptance)),
    ("% платной приемки от реализациии до СПП", lambda r: r.of_gross(r.acceptance)),
    ("Реклама баланс + счет", lambda r: r.ads.paid),
    ("Реклама бонус инф.", lambda r: r.ads.bonus),
    ("Реклама счет", lambda r: r.ads.account),
    ("Реклама баланс", lambda r: r.ads.balance),
    ("Инф: Реклама из ВБ Продвижение (Баланс + счет)", lambda r: r.ads.promotion_info),
    ("Реклама баланс + счет на единицу товара", lambda r: r.per_unit(r.ads.paid)),
    ("% ДРР (доля рекламных расходов) от реализациии до СПП", lambda r: r.of_gross(r.ads.paid)),
    ("% ДРР с бонусом", lambda r: r.of_gross(r.ads.paid + r.ads.bonus)),
    ("ИМИЗР (использование механик искуственного завышения рейтинга)", lambda r: r.rating_abuse),
    ("ИМИЗР на единицу товара", lambda r: r.per_unit(r.rating_abuse)),
    ("% ИМИЗР от реализациии до СПП", lambda r: r.of_gross(r.rating_abuse)),
    ("Отзывы", lambda r: r.reviews),
    ("Отзывы на единицу товара", lambda r: r.per_unit(r.reviews)),
    ("% отзывов от реализациии до СПП", lambda r: r.of_gross(r.reviews)),
    ("Кредит", lambda r: r.credit),
    ("Кредит на единицу товара", lambda r: r.per_unit(r.credit)),
    ("% кредита от реализациии до СПП", lambda r: r.of_gross(r.credit)),
    ("Удержанная за начисленные баллы по лояльности", lambda r: r.loyalty_points),
    ("Удержанная за начисленные баллы по лояльности на единицу", lambda r: r.per_unit(r.loyalty_points)),
    ("% Удержанная за начисленные баллы по лояльности", lambda r: r.of_gross(r.loyalty_points)),
    ("Участие в программе лояльности", lambda r: r.loyalty_program),
    ("Участие в программе лояльности на единицу", lambda r: r.per_unit(r.loyalty_program)),
    ("% Участие в программе лояльности", lambda r: r.of_gross(r.loyalty_program)),
    ("Разовое изменение срока перечисления ДС", lambda r: r.payout_term),
    ("Разовое изменение срока перечисления ДС на единицу", lambda r: r.per_unit(r.payout_term)),
    ("% Разовое изменение срока перечисления ДС", lambda r: r.of_gross(r.payout_term)),
    ("Прочие удержания", lambda r: r.other_deductions),
    ("в т.ч. Выплата за ущерб на складах", lambda r: r.damage_payout),
    ("Прочие удержания на единицу товара", lambda r: r.per_unit(r.other_deductions)),
    ("% прочих удержаний от реализациии до СПП", lambda r: r.of_gross(r.other_deductions)),
    ("Итого стоимость всех услуг ВБ от реализации до СПП", lambda r: r.services_gross),
    (
        "Итого стоимость всех услуг ВБ от реализации до СПП без реклама счет",
        lambda r: r.services_gross_without_account_ads,
    ),
    ("Стоимость услуг ВБ на единицу товара от реализации до СПП", lambda r: r.per_unit(r.services_gross)),
    ("% всех услуг ВБ от реализации до СПП", lambda r: r.of_gross(r.services_gross)),
    ("Итого стоимость всех услуг ВБ от реализации до СПП без кредита", lambda r: r.services_gross_without_credit),
    (
        "Стоимость услуг ВБ от реализации до СПП без кредита на единицу",
        lambda r: r.per_unit(r.services_gross_without_credit),
    ),
    ("% всех услуг ВБ от реализации до СПП без кредита", lambda r: r.of_gross(r.services_gross_without_credit)),
    ("Итого стоимость всех услуг ВБ от реализации после СПП", lambda r: r.services_after),
    (
        "Итого стоимость всех услуг ВБ от реализации после СПП без реклама счет",
        lambda r: r.services_after_without_account_ads,
    ),
    ("Стоимость услуг ВБ на единицу товара от реализации после СПП", lambda r: r.per_unit(r.services_after)),
    ("% всех услуг ВБ от реализации после СПП", lambda r: r.of_after(r.services_after)),
    ("4", lambda r: None),
    ("Итого к оплате", lambda r: r.payout),
    ("Итого к оплате на единицу товара", lambda r: r.per_unit(r.payout)),
    ("Итого к оплате без самовыкупов", lambda r: r.payout),
    ("5", lambda r: None),
    ("Налог", lambda r: r.tax),
    ("НДС", lambda r: ZERO),
    ("НДС к возмещению от услуг", lambda r: ZERO),
    ("Итого к оплате за вычетом налога", lambda r: r.payout_after_tax),
    ("Себестоимость реализованного товара", lambda r: r.cost_of_sales),
    ("Себестоимость Шушары по СС", lambda r: ZERO),
    ("% себестоимости от суммы реализации до СПП", lambda r: r.cost_rate),
    ("Средняя себестоимость на единицу товара", lambda r: r.unit_cost),
    ("Себестоимость остатка от клиента на последнюю неделю", lambda r: r.cost * r.stock_from_client),
    ("Себестоимость остатка до клиента на последнюю неделю", lambda r: r.cost * r.stock_to_client),
    ("Себестоимость остатка на складах на последнюю неделю", lambda r: r.cost * r.stock_in_warehouse),
    ("Себестоимость остатка всего на последнюю неделю", lambda r: r.cost * r.stock_total),
    ("Себестоимость утилизированного товара", lambda r: r.cost * r.disposed),
    ("Сумма кэшбека (раздачи)", lambda r: ZERO),
    ("Себестоимость самовыкупов", lambda r: ZERO),
    ("Сумма самовыкупов", lambda r: ZERO),
    ("Себестоимость DBS", lambda r: r.cost * r.dbs),
    ("6", lambda r: None),
    ("Маркетинг", lambda r: r.marketing),
    ("Маркетинг на единицу", lambda r: r.per_unit(r.marketing)),
    ("% маркетинга от суммы реализации до СПП", lambda r: r.of_gross(r.marketing)),
    ("7", lambda r: None),
    ("Операционная прибыль", lambda r: r.operating_profit),
    ("% от всей операционной прибыли", lambda r: r.profit_share),
    ("Операционная прибыль на единицу", lambda r: r.per_unit(r.operating_profit)),
    ("% прибыли от суммы реализации до СПП", lambda r: r.profit_rate_gross),
    ("% прибыли от суммы реализации после СПП", lambda r: r.profit_rate_after),
    ("% прибыли от себестоимости реализованного товара", lambda r: r.profit_rate_cost),
    ("Компенсация Шушары | Компенсация за утраченный товар", lambda r: ZERO),
)
# Колонки, которые в старом отчёте заполнялись руками или из источников, которых у нас нет.
UNVERIFIED_COLUMNS = (
    "Самовыкупы",
    "Раздачи",
    "Утилизировано",
    "Корректировки вознаграждения",
    "ИМИЗР (использование механик искуственного завышения рейтинга)",
    "Кредит",
    "в т.ч. Выплата за ущерб на складах",
    "НДС",
    "НДС к возмещению от услуг",
    "Маркетинг",
)


def article_rows(
    totals: Iterable[SalesReportTotals],
    *,
    cost_of: Callable[[int], Decimal | None],
    stock_of: Callable[[int, str], Stock],
    ads_of: Callable[[int], AdSpend],
    tax_rate: Decimal,
    stocked: Iterable[tuple[int, str]] = (),
    unallocated_ads: AdSpend | None = None,
) -> list[ArticleRow]:
    """Строки листа за неделю из сложенных строк отчётов этой недели одного кабинета.

    `tax_rate` — доля (0.12 для 12 %). Строка без артикула собирает удержания
    по документам, а не по товару: реклама, отзывы, «Джем», — и списания
    кампаний, которых зеркало не знает (`unallocated_ads`). `stocked` — артикулы
    с остатком: без движения за неделю они тоже строки листа, иначе залежавшийся
    товар выпал бы из «Остатков».
    """
    rows: dict[tuple[int, str], ArticleRow] = {}
    for item in totals:
        key = (item.nm_id, item.tech_size if item.nm_id else "")
        row = rows.get(key)
        if row is None:
            row = rows[key] = ArticleRow(nm_id=item.nm_id, vendor_code=item.vendor_code, tech_size=key[1])
        if not row.vendor_code and item.vendor_code:
            row.vendor_code = item.vendor_code
        _accumulate(row, item)
    for nm_id, tech_size in stocked:
        if nm_id and (nm_id, tech_size) not in rows and stock_of(nm_id, tech_size).in_warehouse > 0:
            rows[(nm_id, tech_size)] = ArticleRow(nm_id=nm_id, tech_size=tech_size)
    if unallocated_ads is not None and (unallocated_ads.balance or unallocated_ads.account or unallocated_ads.bonus):
        blank = rows.setdefault((NO_ARTICLE, ""), ArticleRow(nm_id=NO_ARTICLE))
        blank.ads = AdSpend(
            unallocated_ads.balance, unallocated_ads.account, unallocated_ads.bonus, blank.ads.promotion_info
        )
    for (nm_id, tech_size), row in rows.items():
        row.tax_rate = tax_rate
        row.unit_cost = cost_of(nm_id) if nm_id else None
        stock = stock_of(nm_id, tech_size) if nm_id else Stock()
        row.stock_in_warehouse, row.stock_to_client = stock.in_warehouse, stock.to_client
        row.stock_from_client, row.stock_total = stock.from_client, stock.total
        if nm_id:
            spent = ads_of(nm_id)
            row.ads = AdSpend(spent.balance, spent.account, spent.bonus, spent.promotion_info + row.ads.promotion_info)
    _shares(list(rows.values()))
    return sorted(
        rows.values(), key=lambda row: (row.nm_id == NO_ARTICLE, -row.revenue_gross, row.nm_id, row.tech_size)
    )


def _accumulate(row: ArticleRow, item: SalesReportTotals) -> None:
    gross, after, pay = money(item.gross), money(item.retail_amount), money(item.for_pay)
    if item.doc_type_name == SALE_DOC_TYPE:
        row.sales += item.quantity
        row.sales_gross += gross
        row.sales_after += after
        row.for_pay += pay
        if item.srv_dbs:
            row.dbs += item.quantity
            row.dbs_gross += gross
    elif item.doc_type_name == SALES_RETURN_DOC_TYPE:
        row.returns += item.quantity
        row.returns_gross -= gross
        row.returns_after -= after
        row.for_pay -= pay
    elif item.quantity or gross or after:
        # Строка с деньгами за товар, но не продажа и не возврат: корректировка продаж.
        row.sale_corrections += item.quantity
        row.corrections_gross += gross
        row.corrections_after += after
        row.for_pay += pay
    elif pay:
        row.payout_corrections += pay
        row.payout_corrections_qty += item.rows
        row.for_pay += pay
    row.deliveries += item.delivery_amount
    name = item.bonus_type_name.lower()
    if CANCELLED in name:
        row.refusals += item.return_amount
    logistics = money(item.delivery_service)
    row.logistics += logistics
    if name.startswith(TO_CLIENT):
        row.logistics_to_client += logistics
    elif name.startswith(FROM_CLIENT):
        row.logistics_from_client += logistics
    elif OWN_RETURN in name:
        row.logistics_own_return += logistics
    row.acquiring += money(item.acquiring_fee)
    row.penalties += money(item.penalty)
    row.storage += money(item.paid_storage)
    row.acceptance += money(item.paid_acceptance)
    sign = -1 if item.doc_type_name == SALES_RETURN_DOC_TYPE else 1
    row.loyalty_points += sign * money(item.cashback_amount)
    row.loyalty_program += sign * money(item.cashback_commission_change)
    deduction = money(item.deduction)
    if deduction:
        kind = deduction_kind(item.bonus_type_name)
        if kind == ADVERTISING:
            # Реклама по удержаниям идёт без артикула; по артикулам её даёт рекламный API.
            row.ads = AdSpend(promotion_info=row.ads.promotion_info + deduction)
        elif kind == REVIEWS:
            row.reviews += deduction
        elif kind == CREDIT:
            row.credit += deduction
        elif kind == PAYOUT_TERM:
            row.payout_term += deduction
        elif kind == RATING_ABUSE:
            row.rating_abuse += deduction
        elif kind == OTHER_DEDUCTIONS:
            row.other_deductions += deduction
            if "ущерб" in name:
                row.damage_payout += deduction


def _shares(rows: list[ArticleRow]) -> None:
    """Доли в итогах кабинета: выручки, продаж размера внутри артикула, прибыли."""
    revenue = sum((row.revenue_gross for row in rows), ZERO)
    profit = sum((row.operating_profit for row in rows), ZERO)
    sales_by_article: dict[int, int] = {}
    for row in rows:
        sales_by_article[row.nm_id] = sales_by_article.get(row.nm_id, 0) + row.sales
    for row in rows:
        row.revenue_share = _ratio(row.revenue_gross, revenue)
        row.profit_share = _ratio(row.operating_profit, profit)
        row.size_share = _ratio(Decimal(row.sales), sales_by_article.get(row.nm_id, 0))
