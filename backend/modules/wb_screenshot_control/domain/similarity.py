"""Меры сходства для дедупа скриншотов: текст, время, chatId, картинка и набор сообщений.

Всё чистое и детерминированное. Левенштейн считается битовым алгоритмом Майерса на длинных целых
Python: на текстах до 4000 символов это миллисекунды, а не секунды обычной таблицы.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Literal

from backend.modules.wb_screenshot_control.domain.pipeline_types import ChatMessage

TIME_EXACT = 1.0
TIME_CONFUSED = 0.9
TIME_SAME_DAY = 0.5
"""Совпал только день: у одной из сторон время известно с точностью до дня."""

CHAT_CONFUSED = 0.8

PREFIX_MIN_FACTOR = 2
"""Префиксное сравнение усечённого текста — только если видимый префикс не короче
`PREFIX_MIN_FACTOR × min_text_len`: короткие шаблонные начала («не соответствует описанию…»)
иначе совпадают у разных отзывов. Эвристика, калибруется на исторических скринах."""

# Цифры, которые OCR и VLM путают на мелком шрифте портала.
_CONFUSABLE_DIGITS = frozenset(frozenset(pair) for pair in (("3", "8"), ("6", "8"), ("0", "8"), ("1", "7"), ("5", "6")))


def levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (вставка, удаление, замена — по 1) битовым алгоритмом Майерса."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    m = len(b)
    if m == 0:
        return len(a)
    peq: dict[str, int] = {}
    for i, char in enumerate(b):
        peq[char] = peq.get(char, 0) | (1 << i)
    mask = (1 << m) - 1
    last = 1 << (m - 1)
    pv, mv, score = mask, 0, m
    for char in a:
        eq = peq.get(char, 0)
        xv = eq | mv
        xh = ((((eq & pv) + pv) & mask) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & last:
            score += 1
        elif mh & last:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def ratio(a: str, b: str) -> float:
    """`1 − lev/max_len`; две пустые строки сходства не дают."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return 1.0 - levenshtein(a, b) / longest


def text_similarity(
    a: str | None,
    b: str | None,
    *,
    a_truncated: bool = False,
    b_truncated: bool = False,
    min_text_len: int = 25,
) -> float:
    """S_text по `text_norm`.

    Префиксный режим — только когда усечён ровно один из текстов (свёрнутый «…» против
    развёрнутого) и видимый префикс достаточно длинный; иначе сравнение целиком.
    """
    if not a or not b:
        return 0.0
    if a_truncated != b_truncated:
        shorter = min(len(a), len(b))
        if shorter >= PREFIX_MIN_FACTOR * min_text_len:
            return ratio(a[:shorter], b[:shorter])
    return ratio(a, b)


def _clock(value: datetime, tz: tzinfo) -> str:
    return value.astimezone(tz).strftime("%H%M")


def digits_confused(a: str, b: str) -> bool:
    """Строки одной длины расходятся ровно одной цифрой из пар путаницы."""
    if len(a) != len(b):
        return False
    diff = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    return len(diff) == 1 and frozenset(diff[0]) in _CONFUSABLE_DIGITS


def time_similarity(
    a: datetime | None,
    a_precision: Literal["minute", "day"] | None,
    b: datetime | None,
    b_precision: Literal["minute", "day"] | None,
    tz: tzinfo,
) -> float:
    """S_time по времени отзыва, как его видно на экране (часовой пояс портала).

    1.0 — тот же день и минута; 0.9 — тот же день, время расходится одной цифрой путаницы;
    0.5 — у одной из сторон только день, и дни равны; иначе 0.
    """
    if a is None or b is None:
        return 0.0
    if a.astimezone(tz).date() != b.astimezone(tz).date():
        return 0.0
    if a_precision == "day" or b_precision == "day":
        return TIME_SAME_DAY
    clock_a, clock_b = _clock(a, tz), _clock(b, tz)
    if clock_a == clock_b:
        return TIME_EXACT
    return TIME_CONFUSED if digits_confused(clock_a, clock_b) else 0.0


def chat_similarity(
    a: str | None,
    a_source: Literal["url", "image"] | None,
    b: str | None,
    b_source: Literal["url", "image"] | None,
) -> float:
    """S_chat: 1.0 — равны; 0.8 — до двух символов разницы, если хоть один прочитан с картинки."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    from_image = "image" in (a_source, b_source) and len(a) == len(b)
    if from_image and sum(x != y for x, y in zip(a, b, strict=True)) <= 2:
        return CHAT_CONFUSED
    return 0.0


def hamming(a: int | None, b: int | None) -> int | None:
    """Число различных бит двух 64-битных dHash; нет хеша — нет расстояния."""
    if a is None or b is None:
        return None
    return ((a ^ b) & 0xFFFF_FFFF_FFFF_FFFF).bit_count()


@dataclass(frozen=True, slots=True)
class MessagePair:
    left: int
    right: int
    text_len: int
    score: float
    dated: bool


def _message_time(a: ChatMessage, b: ChatMessage) -> tuple[float, bool]:
    """Сходство времени двух сообщений и то, подтверждено ли оно датой."""
    if a.hour is None or a.minute is None or b.hour is None or b.minute is None:
        return 0.0, False
    dated = None not in (a.day, a.month, b.day, b.month)
    if dated and (a.day, a.month) != (b.day, b.month):
        return 0.0, False
    clock_a, clock_b = f"{a.hour:02d}{a.minute:02d}", f"{b.hour:02d}{b.minute:02d}"
    if clock_a == clock_b:
        return TIME_EXACT, dated
    return (TIME_CONFUSED, dated) if digits_confused(clock_a, clock_b) else (0.0, False)


def client_messages(messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    """Реплики покупателя со временем и текстом. Сообщения продавца — шаблоны, одинаковые в разных
    чатах, ключом кейса они не бывают."""
    return tuple(
        message
        for message in messages
        if message.side != "seller" and message.text_norm and message.hour is not None and message.minute is not None
    )


def matched_messages(
    a: Sequence[ChatMessage],
    b: Sequence[ChatMessage],
    *,
    text_threshold: float,
) -> tuple[MessagePair, ...]:
    """Пары сообщений «то же время (до путаницы цифр) и тот же текст» — жадно, один к одному.

    Порядок перебора фиксирован (по индексам), поэтому результат детерминирован.
    """
    pairs: list[MessagePair] = []
    used: set[int] = set()
    for i, left in enumerate(a):
        best: MessagePair | None = None
        for j, right in enumerate(b):
            if j in used:
                continue
            time_score, dated = _message_time(left, right)
            if time_score < TIME_CONFUSED:
                continue
            text_score = ratio(left.text_norm, right.text_norm)
            if text_score < text_threshold:
                continue
            if best is None or text_score > best.score:
                best = MessagePair(i, j, min(len(left.text_norm), len(right.text_norm)), text_score, dated)
        if best is not None:
            used.add(best.right)
            pairs.append(best)
    return tuple(pairs)
