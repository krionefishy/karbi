"""Часовой пояс менеджера: в нём он видит время отзыва в кабинете WB.

Пояс задаёт сам менеджер на странице автоматизации, по умолчанию Москва. Список закрытый: только пояса,
в которых работают менеджеры, с подписью по-русски. Время на скрине трактуется в поясе загрузившего и
хранится в UTC; распознавание живёт у файла, поэтому моменты пересчитываются под каждый сабмишен
из сырых полей «как на экране» (`rebase_fields`).
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.domain.normalize import ScreenDateTime, resolve_screen_datetime
from backend.modules.wb_screenshot_control.domain.pipeline_types import ChatMessage, ExtractedFields

DEFAULT_TIMEZONE = "Europe/Moscow"

ZONE_CITIES: tuple[tuple[str, str], ...] = (
    ("Europe/Kaliningrad", "Калининград"),
    ("Europe/Moscow", "Москва"),
    ("Europe/Samara", "Самара"),
    ("Asia/Yekaterinburg", "Екатеринбург"),
    ("Asia/Omsk", "Омск"),
    ("Asia/Krasnoyarsk", "Красноярск"),
    ("Asia/Irkutsk", "Иркутск"),
    ("Asia/Yakutsk", "Якутск"),
    ("Asia/Vladivostok", "Владивосток"),
    ("Asia/Magadan", "Магадан"),
    ("Asia/Kamchatka", "Камчатка"),
    ("Europe/Minsk", "Минск"),
    ("Asia/Almaty", "Алматы"),
)
ZONE_IDS: frozenset[str] = frozenset(zone for zone, _ in ZONE_CITIES)


@dataclass(frozen=True, slots=True)
class ZoneOption:
    id: str
    label: str
    utc_offset_minutes: int


def is_known_zone(value: str | None) -> bool:
    return value in ZONE_IDS


def offset_minutes(zone: str, at: datetime | None = None) -> int:
    moment = (at or datetime.now(UTC)).astimezone(ZoneInfo(zone))
    offset = moment.utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def offset_label(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "−"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def zone_options(at: datetime | None = None) -> list[ZoneOption]:
    """Подписи «Москва, UTC+3» по смещению на момент `at` (по умолчанию сейчас)."""
    options = []
    for zone, city in ZONE_CITIES:
        minutes = offset_minutes(zone, at)
        options.append(ZoneOption(zone, f"{city}, {offset_label(minutes)}", minutes))
    return options


def rebase_fields(fields: ExtractedFields, *, timezone: str, received_at: datetime) -> ExtractedFields:
    """Поля файла → поля сабмишена: время отзыва и «вчера/сегодня» реплик в поясе загрузившего.

    Распознавание до пояса менеджера (нет `review_screen`) пересчитать не из чего: момент остаётся
    как есть, а пояс — прежний (`None` значит пояс модуля по умолчанию).
    """
    if fields.review_screen is None and fields.timezone is None:
        return fields
    review_at, precision = fields.review_at, fields.review_time_precision
    if fields.review_screen is not None:
        review_at, precision = resolve_screen_datetime(fields.review_screen, received_at=received_at, timezone=timezone)
    messages = tuple(_message_day(item, timezone, received_at) for item in fields.messages)
    return replace(fields, review_at=review_at, review_time_precision=precision, timezone=timezone, messages=messages)


def _message_day(message: ChatMessage, timezone: str, received_at: datetime) -> ChatMessage:
    if message.days_ago is None:
        return message
    resolved, _ = resolve_screen_datetime(
        ScreenDateTime(days_ago=message.days_ago), received_at=received_at, timezone=timezone
    )
    if resolved is None:
        return message
    local = resolved.astimezone(ZoneInfo(timezone))
    return replace(message, day=local.day, month=local.month)


def messages_in_zone(
    messages: tuple[ChatMessage, ...], *, source: str, target: str, at: datetime
) -> tuple[ChatMessage, ...]:
    """Время реплик «как на экране» в поясе `source` → как его увидел бы менеджер в поясе `target`.

    Реплики сравниваются по часам и минутам на экране; у менеджеров в разных поясах один и тот же
    диалог показан с разным временем, поэтому перед сравнением время сдвигается на разницу поясов.
    """
    if source == target:
        return messages
    delta = offset_minutes(target, at) - offset_minutes(source, at)
    if delta == 0:
        return messages
    return tuple(_shift(item, delta, at) for item in messages)


def _shift(message: ChatMessage, delta: int, at: datetime) -> ChatMessage:
    if message.hour is None or message.minute is None:
        return message
    total = message.hour * 60 + message.minute + delta
    days, minutes = divmod(total, 24 * 60)
    day, month = message.day, message.month
    if days and day is not None and month is not None:
        try:
            shifted = date(at.year, month, day) + timedelta(days=days)
        except ValueError:
            shifted = None
        if shifted is not None:
            day, month = shifted.day, shifted.month
    return replace(message, hour=minutes // 60, minute=minutes % 60, day=day, month=month)
