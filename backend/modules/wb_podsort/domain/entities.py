import math
from dataclasses import dataclass, field
from datetime import date, timedelta

WINDOW_CHOICES = (7, 14)
MAX_COVER_DAYS = 90
MONTHS_SHOWN = 3


def one_decimal(value: float | None) -> float | None:
    """Как среднее и дни покрытия показываются и на странице, и в книге."""
    return round(value, 1) if value is not None else None


@dataclass(frozen=True, slots=True)
class PodsortSettings:
    """Как считать подсорт: окно продаж, на сколько дней везти и какие регионы в сводном листе."""

    window_days: int = 7
    cover_days: int = 7
    regions: tuple[str, ...] = ("Центральный",)


@dataclass(frozen=True, slots=True)
class OrderLine:
    """Заказ из статистики WB — ровно то, что нужно подсорту, без цены и покупателя."""

    barcode: str
    nm_id: int
    vendor_code: str
    subject: str
    tech_size: str
    region: str
    fbs: bool


@dataclass(frozen=True, slots=True)
class DayCount:
    """Заказы баркода за сутки в регионе. `fbs_orders` — ушли со склада продавца."""

    barcode: str
    region: str
    orders: int
    fbs_orders: int


@dataclass(frozen=True, slots=True)
class BarcodeInfo:
    barcode: str
    nm_id: int
    vendor_code: str
    subject: str
    tech_size: str


@dataclass(frozen=True, slots=True)
class Periods:
    """Даты, от которых считается книга: всё — по полным суткам до вчера.

    Сегодняшний день не берётся: заказы за него ещё идут, и окно в семь дней
    с неполным сегодняшним занижало бы среднее.
    """

    today: date

    @property
    def last_day(self) -> date:
        return self.today - timedelta(days=1)

    def window_start(self, days: int) -> date:
        return self.last_day - timedelta(days=days - 1)

    @property
    def months(self) -> tuple[date, ...]:
        """Первые числа трёх месяцев, старший первым; последний — месяц вчерашнего дня."""
        first = self.last_day.replace(day=1)
        months = [first]
        for _ in range(MONTHS_SHOWN - 1):
            first = (first - timedelta(days=1)).replace(day=1)
            months.append(first)
        return tuple(reversed(months))

    def month_end(self, month: date) -> date:
        following = (month + timedelta(days=32)).replace(day=1)
        return min(following - timedelta(days=1), self.last_day)


@dataclass(slots=True)
class RegionFigures:
    """Одна пара «баркод + регион», куда везём."""

    window_orders: int = 0
    stock: int = 0

    def average(self, window_days: int) -> float:
        return self.window_orders / window_days

    def need(self, window_days: int, cover_days: int) -> int:
        """Сколько везти: продажи на `cover_days` вперёд минус то, что уже лежит в регионе.

        Округление как у ROUND в Excel — половина вверх, чтобы расчёт сходился
        с их книгой, где формула считалась так же.
        """
        raw = self.average(window_days) * cover_days - self.stock
        return max(0, math.floor(raw + 0.5))

    def cover(self, window_days: int) -> float | None:
        """На сколько дней хватит остатка при нынешних продажах; None — продаж нет."""
        average = self.average(window_days)
        return self.stock / average if average > 0 else None


@dataclass(slots=True)
class PodsortRow:
    """Баркод кабинета: заказы за месяцы и окна, по регионам, остатки и подсорт."""

    seller_name: str
    info: BarcodeInfo
    month_orders: list[int]
    orders_14: int = 0
    orders_7: int = 0
    window_orders: int = 0
    window_fbs_orders: int = 0
    # Заказы текущего месяца по регионам — как столбцы I…V в образце.
    month_by_region: dict[str, int] = field(default_factory=dict)
    targets: dict[str, RegionFigures] = field(default_factory=dict)
    unplaced_stock: int = 0

    def average(self, window_days: int) -> float:
        return self.window_orders / window_days
