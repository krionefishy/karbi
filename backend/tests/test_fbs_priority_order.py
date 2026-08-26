from backend.modules.wb_fbs_distribution.domain import (
    BASIS_POINTS,
    DEFAULT_REGIONS,
    Region,
    WarehouseSlot,
    priority_order,
    shares_are_whole,
)

REGIONS = [
    Region(code=code, title=title, position=index, share_bp=0) for index, (code, title) in enumerate(DEFAULT_REGIONS)
]


def slot(warehouse_id: int, region: str, position: int = 0) -> WarehouseSlot:
    return WarehouseSlot(warehouse_id=warehouse_id, region_code=region, position=position)


def test_the_queue_takes_one_warehouse_from_each_direction_first() -> None:
    """K is counted in regions: a small stock must reach different directions
    instead of settling in three Moscow sorting centres."""
    slots = [
        slot(1, "moscow", 0),
        slot(2, "moscow", 1),
        slot(3, "moscow", 2),
        slot(4, "volga", 0),
        slot(5, "krasnodar", 0),
    ]

    assert priority_order(slots, REGIONS)[:3] == [1, 4, 5]


def test_a_second_lap_starts_only_after_every_direction_had_one() -> None:
    slots = [slot(1, "moscow", 0), slot(2, "moscow", 1), slot(3, "volga", 0)]

    assert priority_order(slots, REGIONS) == [1, 3, 2]


def test_inside_a_direction_the_operator_order_decides() -> None:
    slots = [slot(10, "moscow", 2), slot(11, "moscow", 0), slot(12, "moscow", 1)]

    assert priority_order(slots, REGIONS) == [11, 12, 10]


def test_warehouses_with_the_same_place_keep_a_stable_order() -> None:
    """Two warehouses left at position zero must not swap between runs, or the
    same stock would land on different warehouses each calculation."""
    slots = [slot(22, "moscow"), slot(11, "moscow"), slot(33, "moscow")]

    assert priority_order(slots, REGIONS) == [11, 22, 33]


def test_a_direction_the_regions_do_not_know_goes_last() -> None:
    """Dropping it would silently lose a warehouse; putting it first would give
    it a priority nobody assigned."""
    slots = [slot(1, "novosibirsk"), slot(2, ""), slot(3, "moscow")]

    assert priority_order(slots, REGIONS) == [3, 1, 2]


def test_an_empty_direction_does_not_hold_up_the_queue() -> None:
    slots = [slot(1, "moscow"), slot(2, "novosibirsk")]

    assert priority_order(slots, REGIONS) == [1, 2]


def test_every_warehouse_appears_exactly_once() -> None:
    slots = [slot(index, code, index) for index, (code, _) in enumerate(DEFAULT_REGIONS * 3)]

    order = priority_order(slots, REGIONS)

    assert sorted(order) == sorted(item.warehouse_id for item in slots)
    assert len(set(order)) == len(order)


def test_shares_must_add_up_to_a_whole_hundred_percent() -> None:
    even = [
        Region(code=code, title=title, position=i, share_bp=1000) for i, (code, title) in enumerate(DEFAULT_REGIONS)
    ]
    assert shares_are_whole(even) is False

    whole = [
        Region(code=code, title=title, position=i, share_bp=share)
        for i, ((code, title), share) in enumerate(
            zip(DEFAULT_REGIONS, (4000, 2000, 1200, 1300, 1000, 500), strict=True)
        )
    ]
    assert sum(region.share_bp for region in whole) == BASIS_POINTS
    assert shares_are_whole(whole) is True
