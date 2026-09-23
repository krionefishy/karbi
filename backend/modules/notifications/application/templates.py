from collections.abc import Callable
from typing import Any

SUBSCRIPTION_CONFIRMED = "subscription.confirmed"
SUBSCRIPTION_INVALID_LINK = "subscription.invalid_link"
SUBSCRIPTION_NO_TOKEN = "subscription.no_token"
SUBSCRIPTION_STOPPED = "subscription.stopped"
SUBSCRIPTION_NOTHING_TO_STOP = "subscription.nothing_to_stop"
TURNOVER_DIGEST = "turnover.digest"
RETURNS_READY = "returns.ready"
RETURNS_TRANSIT = "returns.transit"
RETURNS_REMINDER = "returns.reminder"
RETURNS_CLAIMS = "returns.claims"
RETURNS_CLAIM_DEADLINE = "returns.claim_deadline"
RETURNS_WELCOME = "returns.welcome"
RETURNS_HELP = "returns.help"
RETURNS_LIST = "returns.list"
RETURNS_CODE = "returns.code"
RETURNS_EXTENSION = "returns.extension"
RETURNS_NO_SUBSCRIPTION = "returns.no_subscription"
RETURNS_UNKNOWN = "returns.unknown"

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


# --- возвраты WB -----------------------------------------------------------

_MONTHS = ("янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")


def _day(value: Any) -> str:
    """`2026-09-24` или ISO-момент → «24 сент.»; что не разобралось — как пришло."""
    text = str(value or "")
    try:
        month, day = int(text[5:7]), int(text[8:10])
    except ValueError:
        return text
    if not 1 <= month <= 12:
        return text
    return f"{day} {_MONTHS[month - 1]}"


def _money(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    whole = f"{int(round(amount)):,}".replace(",", "\u202f")
    return f"{whole} ₽"


def _return_line(item: dict[str, Any]) -> str:
    detail = str(item.get("return_type") or "")
    reason = str(item.get("reason") or "")
    if reason:
        detail = f"{detail}, {reason}" if detail else reason
    line = f"• {item.get('title') or 'товар'} · стикер {item.get('sticker') or '—'}"
    return f"{line}\n  {detail}" if detail else line


def _office_block(office: dict[str, Any], *, deadline: bool) -> list[str]:
    count = int(office.get("count") or 0)
    lines = [f"ПВЗ {office.get('address') or 'не указан'} — {count} {_plural(count, 'шт.', 'шт.', 'шт.')}"]
    lines.extend(_return_line(item) for item in office.get("items") or [])
    shown = len(office.get("items") or [])
    if count > shown:
        lines.append(f"  …и ещё {count - shown}")
    if deadline:
        lines.append(f"Бесплатно до {_day(office.get('free_until'))}, забрать до {_day(office.get('deadline'))}")
    return lines


def _returns_ready(params: dict[str, Any]) -> str:
    total = int(params.get("total") or 0)
    lines = [
        f"{params.get('seller_name', 'Магазин')}: {total} "
        f"{_plural(total, 'возврат готов', 'возврата готовы', 'возвратов готовы')} к выдаче"
    ]
    for office in params.get("offices") or []:
        lines.append("")
        lines.extend(_office_block(office, deadline=True))
    code = params.get("code")
    if code:
        lines.append("")
        lines.append(f"Код получения на {_day(params.get('code_date'))}: {code}")
    lines.append("")
    lines.append(
        f"Хранение в ПВЗ {params.get('storage_days', 7)} дн.: первые {params.get('free_days', 3)} бесплатно, "
        "дальше 10 ₽ в день за штуку, потом товар уедет на утилизацию."
    )
    return "\n".join(lines)


def _returns_transit(params: dict[str, Any]) -> str:
    total = int(params.get("total") or 0)
    lines = [
        f"{params.get('seller_name', 'Магазин')}: {total} "
        f"{_plural(total, 'возврат едет', 'возврата едут', 'возвратов едут')} в ПВЗ"
    ]
    for office in params.get("offices") or []:
        lines.append("")
        lines.extend(_office_block(office, deadline=False))
    lines.append("")
    lines.append("Напишу, как только они будут готовы к выдаче.")
    return "\n".join(lines)


def _returns_reminder(params: dict[str, Any]) -> str:
    total = int(params.get("total") or 0)
    day = int(params.get("day") or 0)
    free_days = int(params.get("free_days") or 3)
    if day <= free_days:
        headline = "с завтрашнего дня хранение платное, 10 ₽ в день за штуку"
    else:
        headline = "через два дня товар уедет на утилизацию"
    lines = [
        f"{params.get('seller_name', 'Магазин')}: {total} "
        f"{_plural(total, 'возврат лежит', 'возврата лежат', 'возвратов лежат')} в ПВЗ уже {day} дн. — {headline}"
    ]
    for office in params.get("offices") or []:
        lines.append("")
        lines.extend(_office_block(office, deadline=True))
    return "\n".join(lines)


def _claim_line(claim: dict[str, Any]) -> str:
    lines = [f"• {claim.get('name') or 'товар'} · {_money(claim.get('price'))}"]
    comment = str(claim.get("comment") or "").strip()
    if comment:
        lines.append(f"  «{comment[:300]}»")
    photos = list(claim.get("photos") or [])
    if photos:
        lines.append("  фото: " + " ".join(photos))
    lines.append(f"  ответить до {_day(claim.get('deadline'))}")
    return "\n".join(lines)


def _returns_claims(params: dict[str, Any]) -> str:
    total = int(params.get("total") or 0)
    lines = [
        f"{params.get('seller_name', 'Магазин')}: {total} "
        f"{_plural(total, 'новая заявка', 'новые заявки', 'новых заявок')} на возврат"
    ]
    for claim in params.get("claims") or []:
        lines.append("")
        lines.append(_claim_line(claim))
    lines.append("")
    lines.append(
        f"На ответ {params.get('review_days', 5)} дн.: без решения заявка одобрится сама. "
        "Ответить можно в кабинете WB, раздел «Возвраты покупателей»."
    )
    return "\n".join(lines)


def _returns_claim_deadline(params: dict[str, Any]) -> str:
    total = int(params.get("total") or 0)
    lines = [
        f"{params.get('seller_name', 'Магазин')}: завтра истекает срок ответа по {total} "
        f"{_plural(total, 'заявке', 'заявкам', 'заявкам')} на возврат"
    ]
    for claim in params.get("claims") or []:
        lines.append("")
        lines.append(_claim_line(claim))
    lines.append("")
    lines.append("Без ответа заявка одобрится автоматически.")
    return "\n".join(lines)


_RETURNS_COMMANDS = (
    "/returns — что готово к выдаче и что едет в ПВЗ\n"
    "/qr — код получения на сегодня\n"
    "/extension — расширение для кода получения\n"
    "/help — эта подсказка\n"
    "/stop — отписаться"
)


def _returns_welcome(params: dict[str, Any]) -> str:
    sellers = [str(name) for name in params.get("sellers") or [] if name]
    shops = ", ".join(sellers) if sellers else "магазин"
    return (
        f"Буду присылать возвраты по {shops}: когда товар приедет в ПВЗ и будет готов к выдаче, "
        "сколько дней осталось до платного хранения, и заявки покупателей на возврат.\n\n"
        "Чтобы забирать возвраты по QR прямо из этого чата, нужно ещё два шага:\n"
        "1. Поставьте расширение в браузер, где открыт профиль покупателя владельца кабинета — /extension.\n"
        "2. Введите в расширении код из бота. После этого код получения будет приходить вместе с уведомлением "
        "и по команде /qr.\n\n"
        f"Команды:\n{_RETURNS_COMMANDS}"
    )


def _returns_help(_: dict[str, Any]) -> str:
    return (
        "Бот возвратов Marketplace Auto.\n\n"
        "Подписка: откройте персональную ссылку из интерфейса, раздел «Возвраты WB» → «Ссылка на бота». "
        "Один чат можно подписать на несколько магазинов.\n\n"
        "Код получения в ПВЗ берёт расширение из профиля покупателя владельца кабинета: "
        "поставьте его по /extension и введите код пары из бота.\n\n"
        f"Команды:\n{_RETURNS_COMMANDS}"
    )


def _returns_list(params: dict[str, Any]) -> str:
    blocks: list[str] = []
    for seller in params.get("sellers") or []:
        ready = int(seller.get("ready_total") or 0)
        transit = int(seller.get("transit_total") or 0)
        claims = int(seller.get("open_claims") or 0)
        head = f"{seller.get('name') or 'Магазин'}: готово {ready}, едет {transit}"
        if claims:
            head += f", {claims} {_plural(claims, 'открытая заявка', 'открытые заявки', 'открытых заявок')}"
        lines = [head]
        for office in seller.get("offices") or []:
            lines.append("")
            lines.extend(_office_block(office, deadline=True))
        blocks.append("\n".join(lines))
    if not blocks:
        return "Активных возвратов нет."
    return "\n\n".join(blocks)


def _returns_code(params: dict[str, Any]) -> str:
    lines: list[str] = []
    missing: list[str] = []
    for item in params.get("codes") or []:
        name = str(item.get("name") or "Магазин")
        code = item.get("code")
        if code:
            lines.append(f"{name}: код на {_day(item.get('date'))} — {code}")
        else:
            missing.append(name)
    if missing:
        shops = ", ".join(missing)
        lines.append(
            f"По {shops} кода нет: расширение ещё не подключено или сегодня не прислало код. "
            "Поставьте его по /extension или откройте профиль покупателя владельца — код там в разделе «Доставки»."
        )
    return "\n".join(lines)


def _returns_extension(params: dict[str, Any]) -> str:
    return (
        "Расширение для кода получения пока в сборке — появится здесь же следующим шагом.\n\n"
        "Как оно будет работать: ставится в Chrome или Яндекс Браузер на компьютере, где открыт "
        "профиль покупателя владельца кабинета на wildberries.ru; раз в сутки после полуночи забирает "
        "код получения и отдаёт боту. Пароль расширение не хранит и вход не проходит.\n\n"
        "А пока код получения — в приложении Wildberries владельца, раздел «Доставки»."
    )


def _returns_no_subscription(_: dict[str, Any]) -> str:
    return (
        "Этот чат ещё не подписан ни на один магазин. Откройте персональную ссылку из интерфейса "
        "Marketplace Auto, раздел «Возвраты WB» → «Ссылка на бота»."
    )


def _returns_unknown(params: dict[str, Any]) -> str:
    return f"Команду {params.get('command') or ''} не знаю.\n\nЧто умею:\n{_RETURNS_COMMANDS}"


# Producers send a template id and parameters, never ready-made text: wording
# changes then need no republished events, and the outgoing log keeps both.
TEMPLATES: dict[str, Callable[[dict[str, Any]], str]] = {
    SUBSCRIPTION_CONFIRMED: _confirmed,
    SUBSCRIPTION_INVALID_LINK: _invalid_link,
    SUBSCRIPTION_NO_TOKEN: _no_token,
    SUBSCRIPTION_STOPPED: _stopped,
    SUBSCRIPTION_NOTHING_TO_STOP: _nothing_to_stop,
    TURNOVER_DIGEST: _turnover_digest,
    RETURNS_READY: _returns_ready,
    RETURNS_TRANSIT: _returns_transit,
    RETURNS_REMINDER: _returns_reminder,
    RETURNS_CLAIMS: _returns_claims,
    RETURNS_CLAIM_DEADLINE: _returns_claim_deadline,
    RETURNS_WELCOME: _returns_welcome,
    RETURNS_HELP: _returns_help,
    RETURNS_LIST: _returns_list,
    RETURNS_CODE: _returns_code,
    RETURNS_EXTENSION: _returns_extension,
    RETURNS_NO_SUBSCRIPTION: _returns_no_subscription,
    RETURNS_UNKNOWN: _returns_unknown,
}


def render(template: str, params: dict[str, Any]) -> str:
    if template not in TEMPLATES:
        raise UnknownTemplateError(template)
    return TEMPLATES[template](params)
