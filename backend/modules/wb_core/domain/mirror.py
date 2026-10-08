from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# Виды зеркала: по каждому у селлера своя отметка сбора.
MIRROR_CATALOG = "catalog"
MIRROR_STOCKS = "stocks"
MIRROR_REVIEWS = "reviews"
MIRROR_ORDERS = "orders"
MIRROR_SUPPLIES = "supplies"
MIRROR_CHATS = "chats"
MIRROR_REMAINS = "remains"
MIRROR_SALES_REPORTS = "sales_reports"
MIRROR_ADVERTS = "adverts"
MIRROR_OZON_ACCRUALS = "ozon_accruals"
MIRROR_KINDS = (
    MIRROR_CATALOG,
    MIRROR_STOCKS,
    MIRROR_REVIEWS,
    MIRROR_ORDERS,
    MIRROR_SUPPLIES,
    MIRROR_CHATS,
    MIRROR_REMAINS,
    MIRROR_SALES_REPORTS,
    MIRROR_ADVERTS,
    MIRROR_OZON_ACCRUALS,
)

# Периодичность отчётов реализации: WB формирует и недельные, и суточные из
# одних и тех же строк. Зеркало хранит недельные — по ним считают деньги.
SALES_REPORT_WEEKLY = "weekly"
SALES_REPORT_DAILY = "daily"
# Тип отчёта: 1 — основной, 2 — «по выкупам» (продажи с отложенной оплатой).
# За неделю у кабинета выходят оба, и суммы недели — их сумма.
SALES_REPORT_MAIN = 1
SALES_REPORT_BUYOUT = 2
# Возврат приходит положительной строкой с этим типом документа: знак ставит читающий.
SALES_RETURN_DOC_TYPE = "Возврат"

# Откуда пришло сборочное задание: живой список отдаёт только последние три
# месяца, старше — архив с другим набором полей.
ORDER_SOURCE_LIVE = "live"
ORDER_SOURCE_ARCHIVE = "archive"

# Кто написал в чат. Автосообщения WB тоже приходят от имени продавца.
CHAT_SENDER_CLIENT = "client"
CHAT_SENDER_SELLER = "seller"
# Откуда ушло сообщение продавца: кабинет (люди и сам WB) или публичный API (программы).
CHAT_SOURCE_PORTAL = "seller-portal"
CHAT_SOURCE_API = "seller-public-api"
# Так WB от имени продавца открывает диалог после отзыва с низкой оценкой. Текст
# у всех кабинетов один и продавцом не меняется; сверяется начало — хвост WB правит.
REVIEW_PROMPT_PREFIX = "Здравствуйте. Вы оставили отзыв с низкой оценкой"


@dataclass(frozen=True, slots=True)
class StockFact:
    """Текущий остаток карточки: FBO из отчёта аналитики, FBS суммой по складам продавца.

    `fbo_quantity_full` — с товаром в пути к клиенту и от клиента. Отчёт WB
    поле больше не отдаёт, число складывается у нас; оборачиваемость его не
    читает, но колонка держит прежний смысл.
    """

    article: str
    fbo_quantity: int
    fbo_quantity_full: int
    fbs_quantity: int

    @property
    def total(self) -> int:
        return self.fbo_quantity + self.fbs_quantity


@dataclass(frozen=True, slots=True)
class WarehouseRemain:
    """Остаток баркода на одном складе WB из отчёта «Остатки на складах».

    Кроме настоящих складов отчёт кладёт в тот же список служебные строки:
    «В пути до получателей», «В пути возвраты на склад WB», «Всего находится
    на складах». Они хранятся как пришли — что из них считать, решает тот, кто
    читает.
    """

    barcode: str
    article: str
    tech_size: str
    vendor_code: str
    warehouse_name: str
    quantity: int


@dataclass(frozen=True, slots=True)
class ReviewFact:
    """Отзывы карточки по звёздам. Медиа `None` — не считали, что не спутать с «ни одного»."""

    article: str
    ratings: tuple[int, int, int, int, int]
    with_photo: int | None
    with_video: int | None

    @property
    def total(self) -> int:
        return sum(self.ratings)


@dataclass(frozen=True, slots=True)
class SellerWarehouse:
    """Склад продавца в кабинете WB: с него уходят FBS-заказы."""

    warehouse_id: int
    name: str
    office_id: int


@dataclass(frozen=True, slots=True)
class WbOffice:
    """Объект WB, куда продавец сдаёт поставки. Справочник общий для всех кабинетов."""

    office_id: int
    name: str
    city: str
    address: str


@dataclass(frozen=True, slots=True)
class FbsOrder:
    """Сборочное задание FBS — одна заказанная единица.

    `order_id` — номер задания (`assembly_id` в фин. отчёте), `rid` — `srid` в
    отчётах и статистике: по любому из них отчёт сходится с заданием.
    `supply_id` пустой, пока задание не положили в поставку; `sticker_id` и
    статусы известны только из архива.
    """

    order_id: int
    rid: str
    order_uid: str
    created_at: datetime
    warehouse_id: int
    supply_id: str | None
    office_id: int | None
    nm_id: int
    chrt_id: int
    sku: str
    price_kopecks: int
    sticker_id: int | None
    supplier_status: str | None
    wb_status: str | None
    source: str


@dataclass(frozen=True, slots=True)
class FbsSupply:
    """Поставка FBS: чем и когда сдали задания на объект WB."""

    supply_id: str
    name: str
    created_at: datetime
    closed_at: datetime | None
    scan_dt: datetime | None
    destination_office_id: int | None
    done: bool
    cargo_type: int


@dataclass(frozen=True, slots=True)
class ChatEvent:
    """Сообщение из ленты чатов с покупателями.

    `review_prompt` — автосообщение WB после отзыва с низкой оценкой. Текст
    покупателя хранится только в диалогах, где такое сообщение было: остальная
    переписка отчётам не нужна, а личных данных в ней хватает.
    """

    event_id: str
    chat_id: str
    sender: str
    source: str
    added_at: datetime
    is_new_chat: bool
    review_prompt: bool
    nm_id: int | None
    rid: str | None
    text: str | None
    has_attachments: bool


@dataclass(frozen=True, slots=True)
class SalesReport:
    """Отчёт реализации WB — шапка с итогами, как её отдаёт список отчётов.

    Итоги здесь — слово WB, а не наша сумма строк: по ним сверяется, что
    детализация дочитана целиком. Отчёт неизменяем после создания.
    """

    report_id: int
    report_type: int
    period: str
    date_from: date
    date_to: date
    create_date: date | None
    currency: str
    seller_finance_name: str
    retail_amount_sum: Decimal
    for_pay_sum: Decimal
    delivery_service_sum: Decimal
    paid_storage_sum: Decimal
    paid_acceptance_sum: Decimal
    deduction_sum: Decimal
    penalty_sum: Decimal
    additional_payment_sum: Decimal
    cashback_amount_sum: Decimal
    cashback_discount_sum: Decimal
    cashback_commission_change_sum: Decimal
    bank_payment_sum: Decimal


@dataclass(frozen=True, slots=True)
class SalesReportRow:
    """Строка детализации отчёта реализации — копия ответа WB.

    Имена полей — имена WB в snake_case, чтобы строку можно было сверить с
    документацией без словаря. Не хранятся коды маркировки, реквизиты
    B2B-покупателей и номера УПД: отчётам о деньгах они не нужны, а строк
    в году миллионы.
    """

    rrd_id: int
    report_id: int
    gi_id: int | None
    doc_type_name: str
    seller_oper_name: str
    bonus_type_name: str
    srid: str
    order_id: int | None
    shk_id: int | None
    sticker_id: int | None
    order_uid: str
    trbx_id: str
    order_dt: datetime | None
    sale_dt: datetime | None
    rr_date: date | None
    fix_tariff_date_from: date | None
    fix_tariff_date_to: date | None
    nm_id: int
    vendor_code: str
    title: str
    brand_name: str
    subject_name: str
    tech_size: str
    sku: str
    quantity: int
    retail_price: Decimal
    retail_amount: Decimal
    retail_price_withdisc: Decimal
    sale_percent: Decimal
    commission_percent: Decimal
    spp: Decimal
    product_discount_for_report: Decimal
    seller_promo: Decimal
    seller_promo_id: int | None
    seller_promo_discount: Decimal
    kvw_base: Decimal
    kvw: Decimal
    sup_rating_up: Decimal
    is_kgvp_v2: Decimal
    dlv_prc: Decimal
    ppvz_sales_commission: Decimal
    for_pay: Decimal
    ppvz_reward: Decimal
    acquiring_fee: Decimal
    acquiring_percent: Decimal
    acquiring_bank: str
    payment_processing: str
    vw: Decimal
    vw_nds: Decimal
    delivery_amount: int
    return_amount: int
    delivery_service: Decimal
    rebill_logistic_cost: Decimal
    rebill_logistic_org: str
    penalty: Decimal
    additional_payment: Decimal
    paid_storage: Decimal
    deduction: Decimal
    paid_acceptance: Decimal
    cashback_amount: Decimal
    cashback_discount: Decimal
    cashback_commission_change: Decimal
    installment_cofinancing_amount: Decimal
    wibes_discount_percent: Decimal
    loyalty_id: int | None
    loyalty_discount: Decimal
    warehouse_logistics_coeff: Decimal
    payment_schedule: str
    office_name: str
    ppvz_office_name: str
    ppvz_office_id: int | None
    delivery_method: str
    srv_dbs: bool
    is_b2b: bool
    country: str
    gi_box_type_name: str
    declaration_number: str


@dataclass(frozen=True, slots=True)
class SalesReportTotals:
    """Суммы строк отчёта реализации по артикулу и виду операции — те же цифры WB, только сложенные.

    Ключ — отчёт, месяц операции, артикул, размер и три названия, которыми WB описывает
    строку: по ним читающий сам решает, продажа это, логистика или удержание.
    Знак возврата не применён: `doc_type_name` в ключе, вычитает читающий.
    `gross` — цена продавца до скидки WB (`retailPriceWithDisc`), умноженная
    на количество.
    """

    report_id: int
    # Первый день месяца, в котором WB провёл операцию (`rrDate`); `None` — строка без даты.
    month: date | None
    nm_id: int
    vendor_code: str
    tech_size: str
    doc_type_name: str
    seller_oper_name: str
    bonus_type_name: str
    srv_dbs: bool
    rows: int
    quantity: int
    delivery_amount: int
    return_amount: int
    gross: Decimal
    retail_amount: Decimal
    for_pay: Decimal
    delivery_service: Decimal
    rebill_logistic_cost: Decimal
    penalty: Decimal
    additional_payment: Decimal
    paid_storage: Decimal
    deduction: Decimal
    paid_acceptance: Decimal
    cashback_amount: Decimal
    cashback_discount: Decimal
    cashback_commission_change: Decimal
    acquiring_fee: Decimal
    ppvz_reward: Decimal
    vw: Decimal
    vw_nds: Decimal
    ppvz_sales_commission: Decimal


@dataclass(frozen=True, slots=True)
class AdvertCampaign:
    """Рекламная кампания WB: какие артикулы она продвигает и чем оплачивается."""

    advert_id: int
    name: str
    status: int
    payment_type: str
    bid_type: str
    nm_ids: tuple[int, ...]
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdvertSpend:
    """Списание за кампанию за день с типом оплаты («Баланс», «Счет», «Бонусы») — строка `/adv/v1/upd`."""

    advert_id: int
    day: date
    payment_type: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AdvertNmStat:
    """Статистика кампании по артикулу за день — из `/adv/v3/fullstats`, площадки сложены."""

    advert_id: int
    day: date
    nm_id: int
    views: int
    clicks: int
    orders: int
    shks: int
    atbs: int
    canceled: int
    amount: Decimal
    orders_amount: Decimal


# Откуда в начислении Ozon строка: услуга по отправлению, товарная услуга, услуга без
# товара, по грузоместу, доставка в составе отправления или сама продажа.
OZON_LINE_ITEM = "item"
OZON_LINE_NON_ITEM = "non_item"
OZON_LINE_CONTAINER = "container"
OZON_LINE_DELIVERY = "delivery"
OZON_LINE_SALE = "sale"


@dataclass(frozen=True, slots=True)
class OzonAccrualLine:
    """Одна строка начисления Ozon из `/v1/finance/accrual/by-day` — копия ответа, развёрнутая построчно.

    Начисление — отправление, товар или услуга без товара; внутри него несколько
    сумм с типами из справочника начислений. Продажа — строка `sale` с ценой
    продавца (`sale_amount`), ценой для покупателя (`sale_price`), баллами
    Ozon (`bonus`), софинансированием и вознаграждением за продажу.
    """

    accrual_id: int
    line_no: int
    day: date
    category: str
    unit_number: str
    delivery_schema: str
    line: str
    sku: int
    type_id: int
    quantity: int
    amount: Decimal
    sale_amount: Decimal
    sale_price: Decimal
    sale_commission: Decimal
    bonus: Decimal
    coinvestment: Decimal


def is_review_prompt(sender: str, source: str, text: str | None) -> bool:
    if sender != CHAT_SENDER_SELLER or source != CHAT_SOURCE_PORTAL or not text:
        return False
    return text.lstrip().startswith(REVIEW_PROMPT_PREFIX)
