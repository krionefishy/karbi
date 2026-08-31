from collections.abc import Callable
from typing import Any

SUBSCRIPTION_CONFIRMED = "subscription.confirmed"
SUBSCRIPTION_INVALID_LINK = "subscription.invalid_link"
SUBSCRIPTION_NO_TOKEN = "subscription.no_token"
SUBSCRIPTION_STOPPED = "subscription.stopped"
SUBSCRIPTION_NOTHING_TO_STOP = "subscription.nothing_to_stop"
TURNOVER_DIGEST = "turnover.digest"

# Telegram accepts 4096 characters; a longer list is unreadable anyway.
_DIGEST_LIMIT = 25


class UnknownTemplateError(Exception):
    pass


def _confirmed(params: dict[str, Any]) -> str:
    return (
        "Готово — вы подписаны.\n\n"
        f"Магазин: {params.get('seller_name', '—')}\n"
        f"Уведомления: {params.get('bot_title', 'Marketplace Auto')}\n\n"
        "Чтобы отписаться, отправьте /stop."
    )


def _invalid_link(_: dict[str, Any]) -> str:
    return "Ссылка не подошла: её уже использовали или у неё вышел срок. Попросите новую в интерфейсе Marketplace Auto."


def _no_token(_: dict[str, Any]) -> str:
    return (
        "Здравствуйте! Чтобы получать уведомления, откройте персональную ссылку из интерфейса "
        "Marketplace Auto — по ней бот поймёт, о каком магазине речь."
    )


def _stopped(params: dict[str, Any]) -> str:
    return f"Отписал от уведомлений: {params.get('sellers', '—')}. Вернуться можно по новой ссылке."


def _nothing_to_stop(_: dict[str, Any]) -> str:
    return "В этом чате активных подписок нет."


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def _digest_line(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "Без названия")
    article = item.get("article")
    days = item.get("days")
    stock = int(item.get("stock") or 0)
    fbo, fbs = int(item.get("stock_fbo") or 0), int(item.get("stock_fbs") or 0)
    # Where the stock lies decides how long replenishment takes, so the split is
    # shown whenever the seller works both ways.
    where = f" (склад WB {fbo} + свой {fbs})" if fbo and fbs else ""
    previous = item.get("previous_days")
    trend = ""
    # Дни целые, поэтому дельта видна и без округления: разошлись цифры —
    # значит запас за сутки действительно сдвинулся на день.
    if isinstance(previous, int | float) and isinstance(days, int | float):
        if previous > days:
            trend = f", вчера было {previous:.0f}"
        elif previous < days:
            trend = f", вчера было {previous:.0f} — стало лучше"
    measure = f"{days:.0f}" if isinstance(days, int | float) else "?"
    return f"• {name} ({article})\n  хватит на {measure} дн.{trend} · остаток {stock} шт.{where}"


def _turnover_digest(params: dict[str, Any]) -> str:
    items = list(params.get("items") or [])
    threshold = params.get("threshold_days", 10)
    count = len(items)
    head = (
        f"{params.get('seller_name', 'Магазин')}: {count} "
        f"{_plural(count, 'товар', 'товара', 'товаров')} с запасом меньше {threshold} дн."
    )
    shown = items[:_DIGEST_LIMIT]
    lines = [head, ""] + [_digest_line(item) for item in shown]
    if count > len(shown):
        hidden = count - len(shown)
        tail = _plural(hidden, "товар", "товара", "товаров")
        lines.append(f"\n…и ещё {hidden} {tail} — весь список в Marketplace Auto.")
    return "\n".join(lines)


# Producers send a template id and parameters, never ready-made text: wording
# changes then need no republished events, and the outgoing log keeps both.
TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    SUBSCRIPTION_CONFIRMED: _confirmed,
    SUBSCRIPTION_INVALID_LINK: _invalid_link,
    SUBSCRIPTION_NO_TOKEN: _no_token,
    SUBSCRIPTION_STOPPED: _stopped,
    SUBSCRIPTION_NOTHING_TO_STOP: _nothing_to_stop,
    TURNOVER_DIGEST: _turnover_digest,
}


def render(template: str, params: dict[str, Any]) -> str:
    if template not in TEMPLATES:
        raise UnknownTemplateError(template)
    return TEMPLATES[template](params)
