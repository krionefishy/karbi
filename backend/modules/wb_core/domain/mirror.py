from dataclasses import dataclass
from datetime import datetime

# Виды зеркала: по каждому у селлера своя отметка сбора.
MIRROR_CATALOG = "catalog"
MIRROR_STOCKS = "stocks"
MIRROR_REVIEWS = "reviews"
MIRROR_ORDERS = "orders"
MIRROR_SUPPLIES = "supplies"
MIRROR_KINDS = (MIRROR_CATALOG, MIRROR_STOCKS, MIRROR_REVIEWS, MIRROR_ORDERS, MIRROR_SUPPLIES)

# Откуда пришло сборочное задание: живой список отдаёт только последние три
# месяца, старше — архив с другим набором полей.
ORDER_SOURCE_LIVE = "live"
ORDER_SOURCE_ARCHIVE = "archive"


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
