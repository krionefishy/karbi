from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction


class SharesNotConfigured(Exception):
    """Долей направлений нет, а без них полное покрытие не посчитать."""


@dataclass(frozen=True, slots=True)
class AllocationTarget:
    """Разрешённый склад в очереди распределения."""

    warehouse_id: int
    region_code: str


def largest_remainder(total: int, weights: Sequence[Fraction]) -> list[int]:
    """Разложить целое по долям методом наибольших остатков.

    Дробные квоты считаются точной дробью, а не float: на 69 складах ошибка
    округления двоичной дроби уводит последнюю единицу не туда, и сумма
    перестаёт сходиться.

    При равных дробных частях единица достаётся тому, кто раньше в списке, —
    список уже упорядочен по приоритету, так что «раньше» и значит «важнее».
    """
    if total <= 0 or not weights:
        return [0] * len(weights)
    denominator = sum(weights)
    if denominator <= 0:
        return [0] * len(weights)
    quotas = [Fraction(total) * weight / denominator for weight in weights]
    base = [int(quota) for quota in quotas]
    remainder = total - sum(base)
    order = sorted(range(len(quotas)), key=lambda index: (-(quotas[index] - base[index]), index))
    for index in order[:remainder]:
        base[index] += 1
    return base


def allocate(
    available: int,
    queue: Sequence[AllocationTarget],
    shares_bp: Mapping[str, int],
    *,
    priority_regions: int,
) -> dict[int, int]:
    """Разложить доступное количество по складам кабинета.

    `queue` — уже упорядоченные обходом по кругу разрешённые склады: по одному
    из каждого направления, затем второй круг. `shares_bp` — доли направлений в
    сотых долях процента.

    Две ветки, порог между ними — число разрешённых складов, а не константа 69:
    если для товара разрешено 64 склада, полное покрытие включается с 64 единиц.
    """
    if available <= 0 or not queue:
        return {}
    if available < len(queue):
        return _priority_split(available, queue, priority_regions)
    return _full_coverage(available, queue, shares_bp)


def _priority_split(available: int, queue: Sequence[AllocationTarget], priority_regions: int) -> dict[int, int]:
    """Малый остаток: только первые направления, поровну между ними.

    `priority_regions` считается в направлениях, а не в складах: смысл правила
    в том, чтобы несколько единиц разъехались по разным регионам, а не осели в
    трёх московских СЦ. Очередь построена обходом по кругу, поэтому её первые
    `K` элементов — это и есть по одному складу из первых `K` направлений.
    """
    take = min(max(priority_regions, 1), len(queue), available)
    head = queue[:take]
    amounts = largest_remainder(available, [Fraction(1)] * len(head))
    return {target.warehouse_id: amount for target, amount in zip(head, amounts, strict=True) if amount}


def _full_coverage(available: int, queue: Sequence[AllocationTarget], shares_bp: Mapping[str, int]) -> dict[int, int]:
    """Полное покрытие: сначала проценты, потом минимум в единицу.

    Именно в таком порядке. Если сначала раздать по единице, а проценты
    применить к остатку сверх минимума, заявленные доли перестают выполняться:
    база из N единиц съедает бо́льшую часть небольшого остатка раньше, чем
    проценты вообще начнут работать, и названные логистом 40% превращаются в 17.
    """
    by_region: OrderedDict[str, list[AllocationTarget]] = OrderedDict()
    for target in queue:
        by_region.setdefault(target.region_code, []).append(target)

    # Доля направления, у которого не осталось разрешённых складов,
    # перераспределяется между остальными: иначе сумма плана окажется меньше
    # доступного количества.
    weights = [Fraction(shares_bp.get(code, 0)) for code in by_region]
    if sum(weights) <= 0:
        raise SharesNotConfigured("Доли направлений не заданы")

    plan: dict[int, int] = {}
    for (_, targets), amount in zip(by_region.items(), largest_remainder(available, weights), strict=True):
        inside = largest_remainder(amount, [Fraction(1)] * len(targets))
        for target, value in zip(targets, inside, strict=True):
            plan[target.warehouse_id] = value

    _raise_zeros_to_one(plan, queue)
    return plan


def _raise_zeros_to_one(plan: dict[int, int], queue: Sequence[AllocationTarget]) -> None:
    """Поднять нулевые склады до единицы за счёт самых крупных получателей.

    Донор выбирается заново на каждой единице: снятая разом пачка могла бы
    обнулить самого донора и создать новый ноль. Донор всегда найдётся — если
    бы у всех ненулевых складов была ровно единица, сумма оказалась бы меньше
    числа складов, а в этой ветке её хватает на всех.
    """
    rank = {target.warehouse_id: index for index, target in enumerate(queue)}
    for warehouse_id in [key for key, value in plan.items() if value == 0]:
        donor = max(plan, key=lambda key: (plan[key], rank[key]))
        plan[donor] -= 1
        plan[warehouse_id] = 1
