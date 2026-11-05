import math
from dataclasses import dataclass

COVER_OK = "ok"
COVER_NO_SALES = "no_sales"
COVER_NO_DATA = "no_data"

TONE_CRITICAL = "critical"
TONE_WARNING = "warning"
TONE_CALM = "calm"
TONE_NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class BoardCover:
    """На сколько дней хватит остатка WB при заказах последней недели."""

    orders: int
    rate: float
    days: int | None
    status: str


def compute_board_cover(*, orders: int, stock: int | None, window_days: int) -> BoardCover:
    """Остаток ÷ среднее в день, целые дни вниз.

    Делитель — длина окна, а не дни продаж: так считает аналитик в своей
    таблице, и цифры должны совпасть с её отчётом. Нет заказов — «нет продаж»
    при любом остатке; нет снимка остатков — честное «нет данных», а не ноль.
    """
    rate = orders / window_days
    if orders <= 0:
        return BoardCover(orders, 0.0, None, COVER_NO_SALES)
    if stock is None:
        return BoardCover(orders, rate, None, COVER_NO_DATA)
    return BoardCover(orders, rate, math.floor(stock / rate), COVER_OK)


def cover_tone(cover: BoardCover, *, red_days: int, yellow_days: int) -> str:
    if cover.days is None:
        return TONE_NEUTRAL
    if cover.days < red_days:
        return TONE_CRITICAL
    if cover.days < yellow_days:
        return TONE_WARNING
    return TONE_CALM


def split_article_orders(total: int, shares: dict[str, int]) -> dict[str, int]:
    """Заказы карточки из воронки WB → по баркодам, в долях заказов из Statistics.

    Воронка считает заказы верно, но только по карточке; Statistics знает баркод,
    но теряет часть заказов. Доли берутся из Statistics, сумма — из воронки, и
    после округления сходится с ней до штуки (метод наибольших остатков). Если
    Statistics за окно по карточке молчит, делим поровну: заказы были, а честного
    способа узнать размер нет.
    """
    if not shares:
        return {}
    keys = sorted(shares)
    weights = {key: max(shares[key], 0) for key in keys}
    whole = sum(weights.values())
    if whole == 0:
        weights = dict.fromkeys(keys, 1)
        whole = len(keys)
    exact = {key: total * weights[key] / whole for key in keys}
    result = {key: math.floor(exact[key]) for key in keys}
    left = total - sum(result.values())
    for key in sorted(keys, key=lambda key: (-(exact[key] - result[key]), -weights[key], key))[:left]:
        result[key] += 1
    return result
