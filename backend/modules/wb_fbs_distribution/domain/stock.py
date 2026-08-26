from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class StockLine:
    """Одна строка выгрузки 1С: физический запас одной номенклатуры.

    `characteristic` — размер или вариант; у товара без вариантов пустая.
    `barcode` — то, чем строка потом сопоставляется с размером карточки WB.
    """

    item_id: str
    barcode: str
    name: str
    characteristic: str
    quantity: int


@dataclass(frozen=True, slots=True)
class StockSnapshot:
    """Абсолютный снимок остатков, а не изменения к предыдущему.

    Абсолютный — потому что тогда повторная доставка того же сообщения ничего
    не списывает второй раз, а пропавшая строка честно означает «в 1С этого
    товара больше нет», а не «про него забыли сказать».
    """

    generated_at: datetime
    lines: tuple[StockLine, ...]


def available_units(on_hand: int, reserve: int) -> int:
    """Сколько единиц можно раздать складам после резерва на брак.

    Резерв не берётся, пока остатка не больше него самого, и добирается
    постепенно: `доступно = min(остаток, max(R, остаток - R))`.

    Простое «вычесть R, а на малом остатке не вычитать ничего» разрывно:
    остаток 20 дал бы 20 доступных, а 21 — всего одну, и продавец с бо́льшим
    запасом показывал бы на WB меньше товара. Здесь доступное количество растёт
    вместе с остатком на всём диапазоне.

    >>> [available_units(value, 20) for value in (10, 20, 21, 30, 40, 41, 100)]
    [10, 20, 20, 20, 20, 21, 80]
    """
    if on_hand <= 0 or reserve < 0:
        return max(on_hand, 0) if reserve >= 0 else 0
    return min(on_hand, max(reserve, on_hand - reserve))
