import random
from fractions import Fraction

import pytest

from backend.modules.wb_fbs_distribution.domain import (
    AllocationTarget,
    SharesNotConfigured,
    allocate,
    largest_remainder,
    priority_order,
)
from backend.modules.wb_fbs_distribution.domain.regions import DEFAULT_REGIONS, Region, WarehouseSlot

CODES = [code for code, _ in DEFAULT_REGIONS]
# Москва 40, Приволжье 20, Краснодар 12, Урал 13, Северо-Запад 10, Новосибирск 5.
SHARES = dict(zip(CODES, (4000, 2000, 1200, 1300, 1000, 500), strict=True))
REGIONS = [Region(code=code, title=code, position=index, share_bp=SHARES[code]) for index, code in enumerate(CODES)]


def queue_for(counts: dict[str, int]) -> list[AllocationTarget]:
    """Очередь так, как её строит приоритет: обходом по кругу."""
    slots, warehouse_id = [], 0
    for code, count in counts.items():
        for position in range(count):
            warehouse_id += 1
            slots.append(WarehouseSlot(warehouse_id=warehouse_id, region_code=code, position=position))
    region_of = {slot.warehouse_id: slot.region_code for slot in slots}
    return [AllocationTarget(warehouse_id=wid, region_code=region_of[wid]) for wid in priority_order(slots, REGIONS)]


SIXTY_NINE = {"moscow": 5, "volga": 14, "krasnodar": 12, "ural": 13, "northwest": 13, "novosibirsk": 12}


def test_the_rounding_example_from_the_document() -> None:
    assert largest_remainder(11, [Fraction(w) for w in (40, 25, 20, 10, 5)]) == [4, 3, 2, 1, 1]


def test_small_stock_goes_to_the_first_directions_one_each() -> None:
    """K counts directions: a few pieces must reach different regions rather
    than settle in three Moscow sorting centres."""
    queue = queue_for(SIXTY_NINE)

    plan = allocate(16, queue, SHARES, priority_regions=3)

    assert sorted(plan.values(), reverse=True) == [6, 5, 5]
    assert {queue[index].region_code for index in range(3)} == {"moscow", "volga", "krasnodar"}
    assert set(plan) == {target.warehouse_id for target in queue[:3]}


def test_the_small_stock_examples_the_business_gave() -> None:
    queue = queue_for(SIXTY_NINE)
    plans = {value: allocate(value, queue, SHARES, priority_regions=3) for value in (16, 15, 10, 4, 2)}

    assert [plans[16][queue[i].warehouse_id] for i in range(3)] == [6, 5, 5]
    assert [plans[15][queue[i].warehouse_id] for i in range(3)] == [5, 5, 5]
    assert [plans[10][queue[i].warehouse_id] for i in range(3)] == [4, 3, 3]
    assert [plans[4][queue[i].warehouse_id] for i in range(3)] == [2, 1, 1]
    assert [plans[2].get(queue[i].warehouse_id, 0) for i in range(3)] == [1, 1, 0]


def test_at_the_threshold_every_allowed_warehouse_gets_exactly_one() -> None:
    queue = queue_for(SIXTY_NINE)

    plan = allocate(len(queue), queue, SHARES, priority_regions=3)

    assert set(plan.values()) == {1}
    assert len(plan) == len(queue)


def test_the_threshold_follows_the_allowed_warehouses_not_a_constant() -> None:
    """With five regions banned for a good, full coverage must start at its own
    warehouse count, not at 69."""
    queue = queue_for({"moscow": 5, "volga": 14})

    assert len(allocate(len(queue) - 1, queue, SHARES, priority_regions=3)) == 3
    assert len(allocate(len(queue), queue, SHARES, priority_regions=3)) == len(queue)


def test_shares_apply_to_the_whole_stock_not_to_a_tail() -> None:
    """Handing out one piece each first would eat most of a modest stock before
    the percentages start, and the logist's 40% would show up as 17%."""
    queue = queue_for(SIXTY_NINE)
    moscow = {target.warehouse_id for target in queue if target.region_code == "moscow"}

    for available, expected in ((100, 0.28), (200, 0.36), (1000, 0.40)):
        plan = allocate(available, queue, SHARES, priority_regions=3)
        got = sum(plan[wid] for wid in moscow) / available
        assert got >= expected, (available, got)


def test_a_direction_with_no_warehouses_hands_its_share_to_the_others() -> None:
    """Otherwise the plan would add up to less than the stock and quietly hold
    goods back."""
    queue = queue_for({"moscow": 2, "volga": 2})

    plan = allocate(100, queue, SHARES, priority_regions=3)

    assert sum(plan.values()) == 100


def test_full_coverage_without_shares_is_refused_rather_than_guessed() -> None:
    queue = queue_for(SIXTY_NINE)

    with pytest.raises(SharesNotConfigured):
        allocate(200, queue, dict.fromkeys(CODES, 0), priority_regions=3)


def test_small_stock_still_works_without_shares() -> None:
    """The priority branch never needed percentages, and blocking it would take
    the whole low-turnover catalogue off FBS while the logist thinks."""
    queue = queue_for(SIXTY_NINE)

    plan = allocate(10, queue, dict.fromkeys(CODES, 0), priority_regions=3)

    assert sum(plan.values()) == 10


@pytest.mark.parametrize("available", [69, 70, 76, 100, 137, 138, 500, 1000, 9999])
def test_full_coverage_holds_its_invariants(available: int) -> None:
    queue = queue_for(SIXTY_NINE)

    plan = allocate(available, queue, SHARES, priority_regions=3)

    assert sum(plan.values()) == available
    assert len(plan) == len(queue)
    assert min(plan.values()) >= 1
    assert all(isinstance(value, int) for value in plan.values())


def test_the_plan_never_promises_more_than_there_is() -> None:
    queue = queue_for(SIXTY_NINE)
    for available in range(0, 400):
        plan = allocate(available, queue, SHARES, priority_regions=3)
        assert sum(plan.values()) == available
        assert all(value > 0 for value in plan.values())


def test_the_same_input_always_gives_the_same_plan() -> None:
    """A plan that wobbles between runs would rewrite WB stock for no reason and
    make yesterday's number impossible to explain."""
    queue = queue_for(SIXTY_NINE)
    shuffled = dict(sorted(SHARES.items(), key=lambda item: random.random()))

    first = allocate(317, queue, SHARES, priority_regions=3)
    second = allocate(317, queue, shuffled, priority_regions=3)

    assert first == second


def test_nothing_is_planned_for_an_empty_stock_or_an_empty_queue() -> None:
    queue = queue_for(SIXTY_NINE)

    assert allocate(0, queue, SHARES, priority_regions=3) == {}
    assert allocate(-5, queue, SHARES, priority_regions=3) == {}
    assert allocate(100, [], SHARES, priority_regions=3) == {}


def test_a_single_allowed_warehouse_takes_everything() -> None:
    queue = queue_for({"moscow": 1})

    assert allocate(37, queue, SHARES, priority_regions=3) == {queue[0].warehouse_id: 37}
