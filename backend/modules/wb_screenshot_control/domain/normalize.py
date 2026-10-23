"""Нормализация распознанных полей (архитектура, 6.5) и согласованность двух чтений (6.4).

Только чистые функции: на вход строки как на экране, на выход ключи сравнения. Ничего не знают ни о модели,
ни о базе — поэтому их же можно звать при пересчёте старых распознаваний под новую версию конвейера.
"""

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

Precision = Literal["minute", "day"]
Outcome = Literal["positive", "amended", "deleted"]

CHAT_HOST = "seller.wildberries.ru"
CHAT_PATH = "/chat-with-clients"
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_CHAT_PARAM = re.compile(r"chat_?id\s*[=:]\s*([^&\s]+)", re.IGNORECASE)
# Буквы, которые модель и OCR путают с цифрами. В GUID их не бывает, кроме b: её правим только когда
# она заглавная среди строчных — так модель «прочитала» восьмёрку (c8ea → cBea, этап 0).
_HEX_CONFUSIONS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "s": "5"})
_UPPER_HEX = re.compile(r"[ACDEF]")

_TIME = re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?![\d])")
_WORD_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+([а-я]{3,})\.?(?:\s+(\d{4}))?")
_MONTHS = (
    ("янв",),
    ("фев",),
    ("мар",),
    ("апр",),
    ("мая", "май"),
    ("июн",),
    ("июл",),
    ("авг",),
    ("сен",),
    ("окт",),
    ("ноя",),
    ("дек",),
)
_RELATIVE = {"сегодня": 0, "вчера": 1, "позавчера": 2}

_TRUNCATION_TAILS = ("показать полностью", "читать полностью", "свернуть")
_TAIL = re.compile(r"(?:\s|\.\.\.|…)*(?:(?<![а-я])(?:" + "|".join(_TRUNCATION_TAILS) + r"))?(?:\s|\.\.\.|…)*$")
_SPACES = re.compile(r"\s+")
_DROPPED_CATEGORIES = frozenset({"Mn", "Cf"})

# Латинские двойники кириллицы, которые встречаются в подписях «полож / доп / удал».
_LATIN_TWINS = str.maketrans({"a": "а", "o": "о", "e": "е", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к"})
_CAPTION_PREFIXES: tuple[tuple[str, Outcome], ...] = (("полож", "positive"), ("доп", "amended"), ("удал", "deleted"))
_ARTICLE_LABEL = re.compile(r"^(?:артикул(?:\s+(?:wb|вб|продавца))?|арт\.?|vendor\s*code|sku)\s*[:№#]?\s*", re.I)

# Пары цифр, которые путаются при чтении мелкого шрифта (6.4).
_DIGIT_CONFUSIONS = frozenset(frozenset(pair) for pair in ("38", "68", "08", "17", "56"))


# --- chat_id ---------------------------------------------------------------------------------------------


def chat_id_from_url(url: str | None) -> str | None:
    """GUID из `chatId` адреса чата в кабинете. Чужой хост или путь — None: ссылку не на чат WB не верим."""
    if not url:
        return None
    url = "".join(url.split())
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host != CHAT_HOST or parts.path.rstrip("/") != CHAT_PATH:
        return None
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "chatid":
            return _valid_guid(value.lower())
    return None


def chat_id_from_image(raw: str | None) -> str | None:
    """chatId, прочитанный с картинки: целый URL, `chatId=…`, `1:<GUID>` зеркала или сам GUID.

    Пробелы и переносы убираются, путаницы букв и цифр исправляются; не GUID после этого — None.
    """
    if not raw:
        return None
    text = "".join(raw.split())
    found = _CHAT_PARAM.search(text)
    if found:
        text = found.group(1)
    if ":" in text and text.split(":", 1)[0].isdigit():
        text = text.split(":", 1)[1]
    return _valid_guid(fix_hex_confusions(text))


def chat_id_from_mirror(value: str | None) -> str | None:
    """Зеркало WB хранит `1:<GUID>`; ключ сравнения у нас — только GUID."""
    if not value:
        return None
    return _valid_guid(value.rsplit(":", 1)[-1].strip().lower())


def fix_hex_confusions(text: str) -> str:
    fixed = text.translate(_HEX_CONFUSIONS)
    if not _UPPER_HEX.search(fixed):
        fixed = fixed.replace("B", "8")
    return fixed.lower()


def is_guid(value: str | None) -> bool:
    return bool(value) and _GUID.fullmatch(value or "") is not None


def _valid_guid(text: str) -> str | None:
    if _HEX32.fullmatch(text):
        text = f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"
    return text if _GUID.fullmatch(text) else None


# --- дата и время ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScreenDateTime:
    """Дата и время ровно как на экране: чего не видно, то None. `days_ago` — для «сегодня/вчера»."""

    day: int | None = None
    month: int | None = None
    year: int | None = None
    hour: int | None = None
    minute: int | None = None
    days_ago: int | None = None

    @property
    def has_date(self) -> bool:
        return self.days_ago is not None or (self.day is not None and self.month is not None)

    @property
    def has_time(self) -> bool:
        return self.hour is not None and self.minute is not None

    def digits(self) -> str:
        """Цифры как на экране (ДДММЧЧММ из видимых частей) — для сравнения двух чтений по цифрам."""
        parts = [self.day, self.month] if self.days_ago is None else []
        parts += [self.hour, self.minute]
        return "".join(f"{part:02d}" for part in parts if part is not None)


def parse_screen_datetime(text: str | None) -> ScreenDateTime | None:
    """«22 октября, 06:39», «22 окт 2025 в 06:39», «сегодня, 06:39», «вчера», «22.10.2026 06:39», «06:39»."""
    if not text:
        return None
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    hour = minute = None
    time_match = _TIME.search(value)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        if hour > 23 or minute > 59:
            return None
        value = value[: time_match.start()] + " " + value[time_match.end() :]
    for word, days_ago in _RELATIVE.items():
        if re.search(rf"(?<![а-я]){word}(?![а-я])", value):
            return ScreenDateTime(hour=hour, minute=minute, days_ago=days_ago)
    day = month = year = None
    numeric = _NUMERIC_DATE.search(value)
    worded = _WORD_DATE.search(value)
    if numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        year = _full_year(numeric.group(3))
    elif worded:
        month = _month_number(worded.group(2))
        if month is None:
            return None
        day, year = int(worded.group(1)), _full_year(worded.group(3))
    if day is not None and (month is None or not 1 <= month <= 12 or not 1 <= day <= 31):
        return None
    if day is None and hour is None:
        return None
    return ScreenDateTime(day=day, month=month, year=year, hour=hour, minute=minute)


def screen_datetime_from_numbers(
    *, day: int | None, month: int | None, year: int | None, hour: int | None, minute: int | None
) -> ScreenDateTime | None:
    """Те же поля, что модель вернула числами; вне диапазонов — None (модель ошиблась)."""
    if (hour is None) != (minute is None) or (day is None) != (month is None):
        return None
    if hour is not None and minute is not None and not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if day is not None and month is not None and not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    if day is None and hour is None:
        return None
    if year is not None and not 2000 <= year <= 2100:
        year = None
    return ScreenDateTime(day=day, month=month, year=year, hour=hour, minute=minute)


def resolve_screen_datetime(
    value: ScreenDateTime | None, *, received_at: datetime, timezone: str
) -> tuple[datetime | None, Precision | None]:
    """Момент в UTC. Года нет — ближайшая дата не позже дня получения по часовому поясу кабинета
    (дата из будущего — значит прошлый год). Даты нет совсем — ближайшее такое время не позже получения.
    Без времени — полночь того дня и точность `day`.
    """
    if value is None:
        return None, None
    zone = ZoneInfo(timezone)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    received = received_at.astimezone(zone)
    if value.days_ago is not None:
        day: date | None = received.date() - timedelta(days=value.days_ago)
    elif value.day is not None and value.month is not None:
        day = _nearest_date(value.day, value.month, value.year, received.date())
        if day is None:
            return None, None
    else:
        day = None
    if not value.has_time:
        if day is None:
            return None, None
        return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC), "day"
    assert value.hour is not None and value.minute is not None
    if day is None:
        moment = received.replace(hour=value.hour, minute=value.minute, second=0, microsecond=0)
        if moment > received:
            moment -= timedelta(days=1)
        return moment.astimezone(UTC), "minute"
    local = datetime(day.year, day.month, day.day, value.hour, value.minute, tzinfo=zone)
    return local.astimezone(UTC), "minute"


def _nearest_date(day: int, month: int, year: int | None, received: date) -> date | None:
    if year is not None:
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for candidate_year in range(received.year, received.year - 8, -1):
        try:
            candidate = date(candidate_year, month, day)
        except ValueError:
            continue
        if candidate <= received:
            return candidate
    return None


def _month_number(word: str) -> int | None:
    for index, prefixes in enumerate(_MONTHS, start=1):
        if word.startswith(prefixes):
            return index
    return None


def _full_year(text: str | None) -> int | None:
    if not text:
        return None
    year = int(text)
    return 2000 + year if year < 100 else year


# --- текст ----------------------------------------------------------------------------------------------


def normalize_text(text: str | None) -> str:
    """NFKC → casefold → ё→е → только буквы, цифры и пробелы → схлопнуть пробелы → без хвостов «свернуть».

    Невидимые символы форматирования (Cf: пробел нулевой ширины, мягкий перенос, BOM) выбрасываются, а не
    становятся пробелом: иначе вставка их внутрь слов в DOM разбивает слова и роняет сходство текста.
    """
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    value = _TAIL.sub("", value)
    value = "".join(
        char if char.isalnum() else " " for char in value if unicodedata.category(char) not in _DROPPED_CATEGORIES
    )
    value = _SPACES.sub(" ", value).strip()
    return _TAIL.sub("", value).strip()


def has_truncation_tail(text: str | None) -> bool:
    """Текст на экране обрезан: кончается многоточием или кнопкой «показать полностью»."""
    if not text:
        return False
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").rstrip()
    return value.endswith(("…", "...")) or any(value.endswith(tail) for tail in _TRUNCATION_TAILS)


# --- подпись, имя, артикул -------------------------------------------------------------------------------


def normalize_caption(text: str | None) -> Outcome | None:
    """Подпись менеджера: «полож», «Доп.», «УДАЛЁН», в том числе с латинскими двойниками.
    Ровно один вид исхода в подписи — он; ни одного или несколько разных — None (спросим кнопками).
    """
    if not text:
        return None
    value = unicodedata.normalize("NFKC", text).lower().replace("ё", "е").translate(_LATIN_TWINS)
    words = re.findall(r"[а-я]+", value)
    found = {outcome for word in words for prefix, outcome in _CAPTION_PREFIXES if word.startswith(prefix)}
    return found.pop() if len(found) == 1 else None


def buyer_hmac(name: str | None, key: str) -> str | None:
    """HMAC-SHA256 нормализованного имени, первые 16 hex. Без ключа слабый признак выключен."""
    if not key or not name:
        return None
    value = _SPACES.sub(" ", unicodedata.normalize("NFKC", name).lower().replace("ё", "е")).strip()
    if not value:
        return None
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()[:16]


def normalize_article(value: str | None) -> str | None:
    """nmId — только цифры; иначе vendorCode в верхнем регистре без пробелов."""
    if not value:
        return None
    text = _ARTICLE_LABEL.sub("", unicodedata.normalize("NFKC", value).strip())
    compact = "".join(text.split())
    if not compact:
        return None
    return compact if compact.isdigit() else compact.upper()


# --- согласованность двух чтений (6.4) ------------------------------------------------------------------


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def chat_id_agreement(a: str | None, b: str | None) -> float | None:
    """1.0 — совпали; 0.8 — после исправления путаниц расходятся не больше чем на 2 символа; иначе 0.
    None — второго чтения нет, сравнивать нечего."""
    if not a or not b:
        return None
    left, right = _chat_key(a), _chat_key(b)
    if left == right:
        return 1.0
    return 0.8 if levenshtein(left, right) <= 2 else 0.0


def _chat_key(raw: str) -> str:
    return chat_id_from_image(raw) or fix_hex_confusions("".join(raw.split()))


def time_agreement(a: str | None, b: str | None) -> float | None:
    """По цифрам как на экране: 1.0 — равны; 0.9 — одна цифра из пары путаницы (06:39 / 08:39); иначе 0."""
    if not a or not b:
        return None
    if a == b:
        return 1.0
    if len(a) != len(b):
        return 0.0
    differ = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    return 0.9 if len(differ) == 1 and frozenset(differ[0]) in _DIGIT_CONFUSIONS else 0.0


def datetime_agreement(a: ScreenDateTime | None, b: ScreenDateTime | None) -> float | None:
    """Два чтения даты-времени: с датой, если она числом видна в обоих, иначе только время."""
    if a is None or b is None:
        return None
    if a.days_ago is not None or b.days_ago is not None:
        if a.days_ago is not None and b.days_ago is not None and a.days_ago != b.days_ago:
            return 0.0
        a, b = ScreenDateTime(hour=a.hour, minute=a.minute), ScreenDateTime(hour=b.hour, minute=b.minute)
    elif a.has_date != b.has_date:
        a, b = ScreenDateTime(hour=a.hour, minute=a.minute), ScreenDateTime(hour=b.hour, minute=b.minute)
    elif a.has_time != b.has_time:
        a, b = ScreenDateTime(day=a.day, month=a.month), ScreenDateTime(day=b.day, month=b.month)
    return time_agreement(a.digits(), b.digits())


def text_agreement(a: str | None, b: str | None) -> float | None:
    """1 − lev/max_len по нормализованным строкам."""
    if a is None or b is None:
        return None
    left, right = normalize_text(a), normalize_text(b)
    longest = max(len(left), len(right))
    if longest == 0:
        return 1.0
    return round(1 - levenshtein(left, right) / longest, 4)
