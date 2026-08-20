import uuid
from datetime import date

from backend.modules.wb_turnover.domain import (
    STATUS_INSUFFICIENT,
    STATUS_NO_SALES,
    STATUS_NO_STOCK,
    STATUS_OK,
    ArticleOrders,
    ArticleStock,
    compute_turnover,
)

SELLER = uuid.uuid4()
TODAY = date(2026, 8, 20)
WINDOW = 14


def metric(
    *,
    stock: ArticleStock | None = ArticleStock("123", 42, 0),
    orders: ArticleOrders | None = ArticleOrders("123", 28, 0, date(2026, 8, 6)),
    average_stock: float = 42.0,
    stock_days: int = 14,
):
    return compute_turnover(
        seller_id=SELLER,
        article="123",
        day=TODAY,
        stock=stock,
        orders=orders,
        average_stock=average_stock,
        stock_days=stock_days,
        window_days=WINDOW,
    )


def test_two_weeks_of_orders_become_a_daily_rate_and_days_of_cover() -> None:
    row = metric()

    # 28 orders over the 14 whole days of the window is two a day; 42 in stock
    # lasts three weeks at that rate.
    assert row.avg_daily_orders == 2.0
    assert row.days_of_cover == 21.0
    assert row.status == STATUS_OK


def test_cover_follows_todays_stock_while_turnover_follows_the_average() -> None:
    """The alert must react to the shelf being empty now, not to how full it was."""
    row = metric(stock=ArticleStock("123", 4, 0), average_stock=42.0)

    assert row.days_of_cover == 2.0
    assert row.turnover_days == 21.0


def test_stock_is_summed_across_both_delivery_models() -> None:
    row = metric(stock=ArticleStock("123", 12, 30))

    assert (row.stock_total, row.stock_fbo, row.stock_fbs) == (42, 12, 30)
    assert row.days_of_cover == 21.0


def test_a_товар_on_sale_for_three_days_is_not_averaged_over_fourteen() -> None:
    """Dividing by the whole window would make a new article look five times slower."""
    row = metric(orders=ArticleOrders("123", 12, 0, date(2026, 8, 16)))

    assert row.sales_days == 4
    assert row.avg_daily_orders == 3.0
    assert row.days_of_cover == 14.0


def test_cancellations_are_not_counted_as_sales() -> None:
    row = metric(orders=ArticleOrders("123", 14, 14, date(2026, 8, 6)))

    assert row.avg_daily_orders == 1.0
    assert row.cancelled_count == 14
    assert row.days_of_cover == 42.0


def test_an_empty_shelf_is_not_a_fast_turnover() -> None:
    """Zero stock divides to zero days, which would read as the most urgent alert."""
    row = metric(stock=ArticleStock("123", 0, 0))

    assert row.status == STATUS_NO_STOCK
    assert row.days_of_cover is None
    assert row.alerting is False


def test_an_article_nobody_orders_raises_no_alarm() -> None:
    row = metric(orders=ArticleOrders("123", 0, 3, date(2026, 8, 6)))

    assert row.status == STATUS_NO_SALES
    assert row.days_of_cover is None


def test_without_a_snapshot_we_say_we_do_not_know_instead_of_zero() -> None:
    row = metric(stock=None, average_stock=0.0, stock_days=0)

    assert row.status == STATUS_INSUFFICIENT
    assert row.days_of_cover is None


def test_the_first_day_after_connecting_already_gives_a_number() -> None:
    """Orders backfill instantly; only the average stock needs time to accumulate."""
    row = metric(average_stock=42.0, stock_days=1)

    assert row.stock_days == 1
    assert row.days_of_cover == 21.0
    assert row.status == STATUS_OK
