"""Разбор полей WB: даты без зоны — московские, числа приходят и строками."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def as_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_moment(value: Any) -> datetime | None:
    """`2025-03-31T11:33:53` и `…Z`, и с зоной. Без зоны — время портала WB, московское."""
    text = as_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(UTC)


def as_date(value: Any) -> date | None:
    text = as_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None
