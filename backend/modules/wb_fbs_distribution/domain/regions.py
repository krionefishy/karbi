from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Доли хранятся в сотых долях процента: расчёт остаётся целочисленным, и сумма
# долей проверяется точно, без накопленной ошибки float.
BASIS_POINTS = 10_000

MOSCOW = "moscow"
VOLGA = "volga"
KRASNODAR = "krasnodar"
URAL = "ural"
NORTHWEST = "northwest"
NOVOSIBIRSK = "novosibirsk"

# Порядок подтверждён бизнесом: Москва, Приволжье, Краснодар, Урал,
# Северо-Запад, Новосибирск закрывающий.
DEFAULT_REGIONS: tuple[tuple[str, str], ...] = (
    (MOSCOW, "Москва"),
    (VOLGA, "Приволжье"),
    (KRASNODAR, "Краснодар"),
    (URAL, "Урал"),
    (NORTHWEST, "Северо-Запад"),
    (NOVOSIBIRSK, "Новосибирск"),
)


@dataclass(frozen=True, slots=True)
class Region:
    code: str
    title: str
    position: int
    share_bp: int


@dataclass(frozen=True, slots=True)
class WarehouseSlot:
    """Склад в очереди распределения вместе с его местом в регионе."""

    warehouse_id: int
    region_code: str
    position: int


def shares_are_whole(regions: Iterable[Region]) -> bool:
    """Доли шести направлений должны складываться ровно в 100%."""
    return sum(region.share_bp for region in regions) == BASIS_POINTS


def priority_order(slots: Sequence[WarehouseSlot], regions: Sequence[Region]) -> list[int]:
    """Очередь складов обходом по кругу, а не группами подряд.

    `K` для малого остатка бизнес считает в регионах: смысл в том, чтобы
    несколько единиц разъехались по разным направлениям, а не осели в трёх
    московских СЦ. При обходе по кругу «первые K складов» и «по одному складу
    из первых K регионов» — одно и то же, пока K не больше числа групп, а при
    большем K правило само продолжается вторым кругом.

    Склады неизвестного региона идут в самый хвост: пропустить их — значит
    молча потерять склад, поставить выше — значит отдать ему приоритет, которого
    никто не назначал.
    """
    ranked = {region.code: region.position for region in regions}
    unknown = max(ranked.values(), default=-1) + 1
    by_region: dict[int, list[WarehouseSlot]] = {}
    for slot in slots:
        by_region.setdefault(ranked.get(slot.region_code, unknown), []).append(slot)
    for group in by_region.values():
        group.sort(key=lambda slot: (slot.position, slot.warehouse_id))

    order: list[int] = []
    lap = 0
    while True:
        added = False
        for rank in sorted(by_region):
            group = by_region[rank]
            if lap < len(group):
                order.append(group[lap].warehouse_id)
                added = True
        if not added:
            return order
        lap += 1
