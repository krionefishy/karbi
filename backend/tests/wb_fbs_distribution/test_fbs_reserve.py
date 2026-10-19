from backend.modules.wb_fbs_distribution.domain import available_units

RESERVE = 20


def test_the_numbers_the_business_named() -> None:
    assert [available_units(value, RESERVE) for value in (1000, 100, 89, 88)] == [980, 80, 69, 68]


def test_a_small_stock_keeps_no_reserve_at_all() -> None:
    """Holding twenty back from a fifteen-piece item would take it off FBS
    entirely, and low-turnover goods are most of the catalogue."""
    assert [available_units(value, RESERVE) for value in (1, 10, 19, 20)] == [1, 10, 19, 20]


def test_the_reserve_fills_up_gradually_instead_of_a_cliff() -> None:
    """The literal rule — subtract 20, take nothing below 20 — would give 20
    available at a stock of 20 and just 1 at a stock of 21."""
    assert [available_units(value, RESERVE) for value in (21, 25, 30, 39, 40, 41)] == [20, 20, 20, 20, 20, 21]


def test_available_stock_never_shrinks_as_the_stock_grows() -> None:
    previous = -1
    for on_hand in range(0, 5001):
        current = available_units(on_hand, RESERVE)
        assert current >= previous, on_hand
        previous = current


def test_we_never_offer_more_than_exists_nor_hold_more_than_the_reserve() -> None:
    for on_hand in range(0, 5001):
        available = available_units(on_hand, RESERVE)
        assert 0 <= available <= on_hand
        assert on_hand - available <= RESERVE


def test_a_reserve_of_zero_hands_out_everything() -> None:
    assert [available_units(value, 0) for value in (0, 1, 100)] == [0, 1, 100]


def test_an_empty_or_broken_stock_offers_nothing() -> None:
    assert available_units(0, RESERVE) == 0
    assert available_units(-5, RESERVE) == 0
