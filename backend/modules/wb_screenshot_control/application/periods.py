from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_PERIOD_DAYS = 366


class PeriodError(ValueError):
    """Период перевёрнут или длиннее MAX_PERIOD_DAYS."""


@dataclass(frozen=True, slots=True)
class Period:
    start: date
    end: date

    def bounds(self, timezone: ZoneInfo) -> tuple[datetime, datetime]:
        """Полуинтервал в UTC: от начала первого дня до начала дня после последнего."""
        start = datetime.combine(self.start, datetime.min.time(), timezone)
        return start, datetime.combine(self.end + timedelta(days=1), datetime.min.time(), timezone)


def resolve_period(start: date | None, end: date | None, *, today: date) -> Period:
    """Без дат — сегодня; одна дата — день или от неё до сегодня."""
    end = end or (start if start and start > today else today)
    start = start or end
    if start > end:
        raise PeriodError("Начало периода позже конца")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise PeriodError(f"Период длиннее {MAX_PERIOD_DAYS} дней")
    return Period(start, end)


def preliminary(mode: str, period: Period, kpi_from: str) -> bool:
    """Пробные цифры: теневой режим или период, начавшийся раньше боевого KPI."""
    if mode != "enforce":
        return True
    return bool(kpi_from) and period.start < date.fromisoformat(kpi_from)
