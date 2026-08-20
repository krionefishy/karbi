import uuid
from dataclasses import dataclass
from datetime import date, timedelta

_ONE_DAY = timedelta(days=1)

STATUS_OK = "ok"
STATUS_NO_STOCK = "no_stock"
STATUS_NO_SALES = "no_sales"
STATUS_INSUFFICIENT = "insufficient_data"


@dataclass(frozen=True, slots=True)
class ArticleStock:
    article: str
    fbo: int
    fbs: int

    @property
    def total(self) -> int:
        return self.fbo + self.fbs


@dataclass(frozen=True, slots=True)
class ArticleOrders:
    article: str
    orders: int
    cancelled: int
    first_order: date | None


@dataclass(frozen=True, slots=True)
class TurnoverRow:
    seller_id: uuid.UUID
    article: str
    date: date
    stock_fbo: int
    stock_fbs: int
    stock_total: int
    avg_stock: float
    orders_count: int
    cancelled_count: int
    avg_daily_orders: float
    days_of_cover: float | None
    turnover_days: float | None
    stock_days: int
    sales_days: int
    status: str

    @property
    def alerting(self) -> bool:
        return self.status == STATUS_OK and self.days_of_cover is not None


def compute_turnover(
    *,
    seller_id: uuid.UUID,
    article: str,
    day: date,
    stock: ArticleStock | None,
    orders: ArticleOrders | None,
    average_stock: float,
    stock_days: int,
    window_days: int,
) -> TurnoverRow:
    """Turn one article's stock and orders into the two numbers we report.

    `days_of_cover` answers the question the alert is about — how long what is
    on the shelf right now will last at the current rate — and is exact from the
    first day, because both halves are known immediately.

    `turnover_days` is the bookkeeping figure over the same period. It needs the
    average stock, and Wildberries serves no stock history, so it is only as
    honest as the number of days we have been collecting (`stock_days`).

    The divisor is the days the article was actually on sale inside the window,
    not the window itself: a товар first ordered three days ago would otherwise
    look like it sells five times slower than it does.
    """
    ordered = orders.orders if orders else 0
    cancelled = orders.cancelled if orders else 0
    sales_days = _sales_days(day, orders.first_order if orders else None, window_days)
    rate = ordered / sales_days if sales_days else 0.0
    stock_fbo = stock.fbo if stock else 0
    stock_fbs = stock.fbs if stock else 0
    total = stock_fbo + stock_fbs

    status = STATUS_OK
    if stock is None:
        # No snapshot at all: we do not know the stock, so we must not claim it is zero.
        status = STATUS_INSUFFICIENT
    elif total == 0:
        status = STATUS_NO_STOCK
    elif ordered == 0:
        status = STATUS_NO_SALES

    days_of_cover = round(total / rate, 2) if status == STATUS_OK and rate > 0 else None
    turnover_days = round(average_stock / rate, 2) if rate > 0 and average_stock > 0 else None
    return TurnoverRow(
        seller_id=seller_id,
        article=article,
        date=day,
        stock_fbo=stock_fbo,
        stock_fbs=stock_fbs,
        stock_total=total,
        avg_stock=round(average_stock, 2),
        orders_count=ordered,
        cancelled_count=cancelled,
        avg_daily_orders=round(rate, 4),
        days_of_cover=days_of_cover,
        turnover_days=turnover_days,
        stock_days=stock_days,
        sales_days=sales_days,
        status=status,
    )


def _sales_days(day: date, first_order: date | None, window_days: int) -> int:
    """How many days of the window the article could actually be ordered on.

    The window ends yesterday: today is only a few hours old at calculation
    time, and counting it as a full day would understate every rate.
    """
    if first_order is None:
        return 0
    last_day = day - _ONE_DAY
    if first_order > last_day:
        return 1
    return max(1, min(window_days, (last_day - first_order).days + 1))
