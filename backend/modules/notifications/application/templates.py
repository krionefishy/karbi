import base64
import io
from collections.abc import Callable
from typing import Any

import segno

SUBSCRIPTION_CONFIRMED = "subscription.confirmed"
SUBSCRIPTION_INVALID_LINK = "subscription.invalid_link"
SUBSCRIPTION_NO_TOKEN = "subscription.no_token"
SUBSCRIPTION_STOPPED = "subscription.stopped"
SUBSCRIPTION_NOTHING_TO_STOP = "subscription.nothing_to_stop"
TURNOVER_DIGEST = "turnover.digest"
RETURNS_DIGEST = "returns.digest"
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


def _pieces(count: int) -> str:
    return f"{count} шт."


def _office_lines(title: str, offices: list[dict[str, Any]], total: int) -> list[str]:
    """«Готово к выдаче: 9 шт.» и адреса с количеством — и больше ничего."""
    lines = [f"{title}: {_pieces(total)}"]
    for office in offices[:30]:
        lines.append(f"• {office.get('address') or 'адрес не указан'} — {_pieces(int(office.get('count') or 0))}")
    if len(offices) > 30:
        lines.append(f"…и ещё {len(offices) - 30} ПВЗ")
    return lines


def _code_line(name: str, params: dict[str, Any]) -> str:
    code = params.get("code")
    if code:
        return f"{name} — код для получения доставки: {code}"
    if params.get("no_install"):
        return f"{name} — кода нет: расширение не подключено (/extension)"
    return f"{name} — кода на сегодня нет"


def _returns_digest(params: dict[str, Any]) -> str:
    """Слот: код с QR, готовые и едущие по адресам, кто залежался, что не так с расширением."""
    name = str(params.get("seller_name") or "Магазин")
    blocks: list[list[str]] = [[_code_line(name, params)]]
    ready_total = int(params.get("ready_total") or 0)
    if ready_total:
        blocks.append(_office_lines("Готово к выдаче", list(params.get("ready") or []), ready_total))
    transit_total = int(params.get("transit_total") or 0)
    if transit_total:
        blocks.append(_office_lines("Едет в ПВЗ", list(params.get("transit") or []), transit_total))
    overdue = int(params.get("overdue_total") or 0)
    tail: list[str] = []
    if overdue:
        tail.append(f"Лежат дольше {params.get('free_days', 3)} дн.: {_pieces(overdue)} — хранение платное")
    tail.extend(str(alert) for alert in params.get("alerts") or [])
    if tail:
        blocks.append(tail)
    return "\n\n".join("\n".join(block) for block in blocks)


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
        f"Буду присылать возвраты по {shops}: в 9:00, 12:00, 15:00 и 18:00 — код получения с QR и адреса ПВЗ, "
        "где что готово к выдаче. Если ничего не изменилось, молчу.\n\n"
        "Чтобы код приходил сюда, нужно ещё два шага:\n"
        "1. Поставьте расширение в браузер, где открыт профиль покупателя владельца кабинета — /extension.\n"
        "2. Введите в расширении код из бота.\n\n"
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
        name = str(seller.get("name") or "Магазин")
        ready_total = int(seller.get("ready_total") or 0)
        transit_total = int(seller.get("transit_total") or 0)
        lines = [name]
        if ready_total:
            lines.extend(_office_lines("Готово к выдаче", list(seller.get("ready") or []), ready_total))
        if transit_total:
            lines.extend(_office_lines("Едет в ПВЗ", list(seller.get("transit") or []), transit_total))
        if not ready_total and not transit_total:
            lines.append("Активных возвратов нет.")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "Активных возвратов нет."


def _returns_code(params: dict[str, Any]) -> str:
    """Код дня одного магазина; с картинкой этот текст становится её подписью (до 1024 знаков)."""
    name = str(params.get("name") or "Магазин")
    if not params.get("code"):
        if params.get("no_install"):
            return (
                f"{name}: расширение не подключено, кода нет. Поставьте его по /extension "
                "или возьмите код в приложении Wildberries владельца, раздел «Доставки»."
            )
        if params.get("requested"):
            return (
                f"{name}: кода на сегодня ещё нет — попросил расширение обновить, пришлю сюда, как только придёт. "
                "Обычно это несколько минут; если браузер с расширением выключен, код не придёт."
            )
        return f"{name}: кода на сегодня нет."
    offices = list(params.get("offices") or [])
    lines = [_code_line(name, params), ""]
    if offices:
        lines.extend(_office_lines("Готово к выдаче", offices, sum(int(o.get("count") or 0) for o in offices)))
    else:
        lines.append("Готовых к выдаче возвратов нет.")
    return "\n".join(lines)


def _returns_extension(params: dict[str, Any]) -> str:
    codes = params.get("codes") or []
    ttl = int(params.get("ttl_minutes") or 15)
    lines = [
        "Расширение берёт код получения из профиля покупателя владельца кабинета и присылает его сюда.",
        "",
        f"1. Скачайте архив: {params.get('download_url') or '—'}",
        "2. Распакуйте, откройте chrome://extensions (в Яндекс Браузере — browser://extensions), включите "
        "«Режим разработчика» и нажмите «Загрузить распакованное расширение», выберите папку.",
        "3. В том же браузере войдите на wildberries.ru под телефоном владельца кабинета. Если это Dolphin или "
        "другой антидетект-браузер, оставьте вкладку wildberries.ru открытой: новые вкладки они расширению не дают.",
        f"4. Откройте настройки расширения (адрес сервиса уже подставлен) и введите код (действует {ttl} мин.):",
    ]
    for item in codes:
        lines.append(f"   {item.get('name') or 'магазин'}: {item.get('code')}")
    lines.append("")
    lines.append(
        "Пароль расширение не хранит и вход не проходит. Раз в сутки после полуночи оно забирает код и "
        "отдаёт боту; если сессия слетит, напишу сюда."
    )
    return "\n".join(lines)


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
    RETURNS_DIGEST: _returns_digest,
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


# --- вложения ----------------------------------------------------------------


def _qr_photo(qr: Any, caption: str) -> dict[str, Any] | None:
    if not isinstance(qr, str) or not qr:
        return None
    buffer = io.BytesIO()
    segno.make(qr, error="q").save(buffer, kind="png", scale=10, border=2)
    return {"kind": "photo", "png_base64": base64.b64encode(buffer.getvalue()).decode(), "caption": caption[:1024]}


def _returns_digest_photo(params: dict[str, Any]) -> dict[str, Any] | None:
    return _qr_photo(params.get("qr"), _code_line(str(params.get("seller_name") or ""), params).strip(" —"))


def _returns_code_photo(params: dict[str, Any]) -> dict[str, Any] | None:
    return _qr_photo(params.get("qr"), _code_line(str(params.get("name") or ""), params).strip(" —"))


# Картинка рядом с текстом. Рисуется при постановке в очередь, чтобы доставка
# не зависела от библиотеки; строка QR — секрет дня, картинка живёт в нашей базе.
ATTACHMENTS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    RETURNS_DIGEST: _returns_digest_photo,
    RETURNS_CODE: _returns_code_photo,
}


def render_attachment(template: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if template not in TEMPLATES:
        raise UnknownTemplateError(template)
    renderer = ATTACHMENTS.get(template)
    return renderer(params) if renderer else None
