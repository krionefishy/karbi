import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True, slots=True)
class SendWindow:
    start: time = time(9, 0)
    end: time = time(22, 0)
    timezone: ZoneInfo = MOSCOW

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(self.timezone).time()
        return self.start <= local < self.end

    def next_opening(self, moment: datetime) -> datetime:
        """Ближайшее открытие окна после `moment`, когда окно закрыто."""
        local = moment.astimezone(self.timezone)
        day = local.date() if local.time() < self.start else local.date() + timedelta(days=1)
        return datetime.combine(day, self.start, tzinfo=self.timezone).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Pace:
    """Шаг рассылки: интервал и случайная добавка к нему."""

    interval: timedelta = timedelta(hours=1)
    jitter_min: timedelta = timedelta(minutes=1)
    jitter_max: timedelta = timedelta(minutes=10)

    def next_gap(self, rng: random.Random) -> timedelta:
        low, high = self.jitter_min.total_seconds(), self.jitter_max.total_seconds()
        return self.interval + timedelta(seconds=rng.uniform(low, high))


def next_send_at(
    last_sent_at: datetime,
    *,
    window: SendWindow,
    pace: Pace,
    rng: random.Random,
) -> datetime:
    """Когда писать следующее сообщение: шаг от прошлого, но не вне окна.

    Если шаг падает после закрытия окна или до его открытия, сообщение уходит
    на открытие ближайшего окна плюс небольшой случайный сдвиг, чтобы утром
    сообщения по разным диалогам не уходили одной пачкой.
    """
    candidate = last_sent_at + pace.next_gap(rng)
    if window.contains(candidate):
        return candidate
    return window.next_opening(candidate) + timedelta(seconds=rng.uniform(0, pace.jitter_max.total_seconds()))


def first_send_at(now: datetime, *, window: SendWindow, pace: Pace, rng: random.Random) -> datetime:
    """Когда писать первое сообщение кампании: сразу, если окно открыто, иначе с его открытием."""
    if window.contains(now):
        return now
    return window.next_opening(now) + timedelta(seconds=rng.uniform(0, pace.jitter_max.total_seconds()))
