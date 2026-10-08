"""Периоды отчёта: недели и месяцы, собранные из отчётов реализации WB.

Неделя — отчёты одной ISO-недели: на границе месяца WB иногда закрывает
отчёт и открывает новый, и тогда их два. Месяц так не собрать — чаще отчёт
недели на стыке месяцев один; его строки делятся по дате операции WB.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

GRANULARITY_WEEK = "week"
GRANULARITY_MONTH = "month"
GRANULARITIES = (GRANULARITY_WEEK, GRANULARITY_MONTH)

MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


@dataclass(frozen=True, slots=True, order=True)
class Period:
    """Неделя или месяц года. `number` — номер ISO-недели либо месяца."""

    year: int
    number: int
    granularity: str

    @property
    def key(self) -> str:
        prefix = "W" if self.granularity == GRANULARITY_WEEK else "M"
        return f"{self.year}-{prefix}{self.number:02d}"

    def label(self, date_from: date, date_to: date) -> str:
        if self.granularity == GRANULARITY_MONTH:
            return MONTHS[self.number - 1]
        return f"{self.number} ({date_from:%d.%m}-{date_to:%d.%m})"


def period_of(day: date, granularity: str) -> Period:
    """Период, в который попадает отчёт, начавшийся в `day`.

    Год недели — ISO-год: неделя с 29 декабря относится к следующему году,
    и её отчёт не должен задвоиться в двух годах.
    """
    if granularity == GRANULARITY_MONTH:
        return Period(day.year, day.month, GRANULARITY_MONTH)
    iso = day.isocalendar()
    return Period(iso.year, iso.week, GRANULARITY_WEEK)


def period_bounds(period: Period, date_from: date, date_to: date) -> tuple[date, date]:
    """Границы периода для подписи: у недели — даты её отчётов, у месяца — сам месяц."""
    if period.granularity == GRANULARITY_WEEK:
        return date_from, date_to
    first = date(period.year, period.number, 1)
    return first, date(period.year, period.number, monthrange(period.year, period.number)[1])


def period_days(period: Period) -> tuple[date, date]:
    """Календарные границы периода: понедельник–воскресенье ISO-недели либо месяц."""
    if period.granularity == GRANULARITY_WEEK:
        first = date.fromisocalendar(period.year, period.number, 1)
        return first, first + timedelta(days=6)
    return period_bounds(period, first := date(period.year, period.number, 1), first)


def parse_period(key: str) -> Period:
    """Обратное к `Period.key`; на мусор — `ValueError`."""
    year, _, rest = key.partition("-")
    if len(rest) < 2 or rest[0] not in "WM" or not year.isdigit() or not rest[1:].isdigit():
        raise ValueError(key)
    granularity = GRANULARITY_WEEK if rest[0] == "W" else GRANULARITY_MONTH
    number = int(rest[1:])
    if not 1 <= number <= (53 if granularity == GRANULARITY_WEEK else 12):
        raise ValueError(key)
    return Period(int(year), number, granularity)
