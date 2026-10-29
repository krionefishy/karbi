import pytest

from backend.modules.wb_turnover.domain import (
    COVER_NO_DATA,
    COVER_NO_SALES,
    COVER_OK,
    TONE_CALM,
    TONE_CRITICAL,
    TONE_NEUTRAL,
    TONE_WARNING,
    compute_board_cover,
    cover_tone,
)


def test_cover_is_stock_over_the_daily_average_rounded_down() -> None:
    cover = compute_board_cover(orders=483, stock=432, window_days=7)
    assert (cover.rate, cover.days, cover.status) == (69.0, 6, COVER_OK)


def test_the_divisor_is_the_window_not_the_days_the_article_sold() -> None:
    assert compute_board_cover(orders=7, stock=10, window_days=7).days == 10


def test_a_stock_under_a_day_is_zero_days_not_nothing() -> None:
    cover = compute_board_cover(orders=495, stock=28, window_days=7)
    assert cover.days == 0
    assert cover.status == COVER_OK


def test_no_orders_means_no_sales_whatever_the_stock() -> None:
    assert compute_board_cover(orders=0, stock=500, window_days=7).status == COVER_NO_SALES
    assert compute_board_cover(orders=0, stock=0, window_days=7).days is None
    assert compute_board_cover(orders=0, stock=None, window_days=7).status == COVER_NO_SALES


def test_without_a_stock_snapshot_the_answer_is_no_data_not_zero() -> None:
    cover = compute_board_cover(orders=14, stock=None, window_days=7)
    assert (cover.days, cover.status) == (None, COVER_NO_DATA)


def test_an_empty_shelf_with_orders_is_zero_days() -> None:
    assert compute_board_cover(orders=14, stock=0, window_days=7).days == 0


@pytest.mark.parametrize(
    ("days", "tone"),
    [(0, TONE_CRITICAL), (9, TONE_CRITICAL), (10, TONE_WARNING), (19, TONE_WARNING), (20, TONE_CALM)],
)
def test_the_tone_follows_the_thresholds(days: int, tone: str) -> None:
    cover = compute_board_cover(orders=7, stock=days, window_days=7)
    assert cover.days == days
    assert cover_tone(cover, red_days=10, yellow_days=20) == tone


def test_a_row_without_days_has_no_tone() -> None:
    cover = compute_board_cover(orders=0, stock=5, window_days=7)
    assert cover_tone(cover, red_days=10, yellow_days=20) == TONE_NEUTRAL
