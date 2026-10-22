import random
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.modules.wb_screenshot_control.domain.pipeline_types import ChatMessage
from backend.modules.wb_screenshot_control.domain.similarity import (
    chat_similarity,
    client_messages,
    digits_confused,
    hamming,
    levenshtein,
    matched_messages,
    ratio,
    text_similarity,
    time_similarity,
)

MSK = ZoneInfo("Europe/Moscow")
AT = datetime(2026, 10, 20, 3, 39, tzinfo=UTC)  # 06:39 по Москве


def table_levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("", "", 0), ("", "абв", 3), ("kitten", "sitting", 3), ("отзыв", "отзыв", 0), ("ёж", "еж", 1), ("ab", "ba", 2)],
)
def test_levenshtein_known_values(a: str, b: str, distance: int) -> None:
    assert levenshtein(a, b) == distance
    assert levenshtein(b, a) == distance


def test_levenshtein_matches_the_plain_table_on_random_strings() -> None:
    rng = random.Random(42)
    for _ in range(400):
        a = "".join(rng.choice("абвгд ") for _ in range(rng.randint(0, 90)))
        b = "".join(rng.choice("абвгд ") for _ in range(rng.randint(0, 90)))
        assert levenshtein(a, b) == table_levenshtein(a, b)


def test_levenshtein_is_fast_on_long_texts() -> None:
    rng = random.Random(1)
    a = "".join(rng.choice("абвгдежзийклмнопрст ") for _ in range(4000))
    b = "".join(char if rng.random() > 0.1 else "я" for char in a)
    started = time.perf_counter()
    distance = levenshtein(a, b)
    assert time.perf_counter() - started < 0.5
    assert 0 < distance <= 4000


def test_ratio() -> None:
    assert ratio("", "") == 0.0
    assert ratio("abcd", "abcd") == 1.0
    assert ratio("a" * 10, "a" * 9 + "b") == pytest.approx(0.9)


LONG = "не соответствует описанию цвет другой размер маломерит шов кривой нитки торчат"


def test_text_similarity_empty_is_zero() -> None:
    assert text_similarity(None, "x") == 0.0
    assert text_similarity("", "") == 0.0


def test_truncated_text_matches_its_expansion_by_prefix() -> None:
    shown = LONG[:55]
    assert text_similarity(shown, LONG, a_truncated=True) == 1.0
    assert text_similarity(shown, LONG) < 0.9


def test_prefix_mode_needs_a_long_enough_prefix() -> None:
    shown = LONG[:49]
    assert text_similarity(shown, LONG, a_truncated=True, min_text_len=25) < 0.9
    assert text_similarity(LONG[:50], LONG, a_truncated=True, min_text_len=25) == 1.0


def test_prefix_mode_only_when_exactly_one_is_truncated() -> None:
    assert text_similarity(LONG[:60], LONG, a_truncated=True, b_truncated=True) < 0.9


@pytest.mark.parametrize(
    ("a", "b", "confused"),
    [
        ("0639", "0839", True),
        ("1015", "1075", True),
        ("0639", "0640", False),
        ("0639", "0838", False),
        ("063", "0639", False),
    ],
)
def test_digit_confusion(a: str, b: str, confused: bool) -> None:
    assert digits_confused(a, b) is confused


def test_time_similarity() -> None:
    assert time_similarity(AT, "minute", AT, "minute", MSK) == 1.0
    assert time_similarity(AT, "minute", AT + timedelta(hours=2), "minute", MSK) == 0.9
    assert time_similarity(AT, "minute", AT + timedelta(minutes=1), "minute", MSK) == 0.0
    assert time_similarity(AT, "minute", AT + timedelta(days=1), "minute", MSK) == 0.0
    assert time_similarity(AT, "day", AT + timedelta(hours=5), "minute", MSK) == 0.5
    assert time_similarity(AT, "day", AT + timedelta(days=1), "minute", MSK) == 0.0
    assert time_similarity(None, None, AT, "minute", MSK) == 0.0


def test_time_similarity_compares_the_day_in_portal_time() -> None:
    late_evening = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)  # 20 октября 01:30 по Москве
    assert time_similarity(late_evening, "day", AT, "day", MSK) == 0.5


CHAT = "0b8f3c2a-1d4e-4f6a-9b7c-2e5d8a1f3c40"


def test_chat_similarity() -> None:
    near = CHAT[:-2] + "99"
    far = CHAT[:-3] + "999"
    assert chat_similarity(CHAT, "url", CHAT, "url") == 1.0
    assert chat_similarity(CHAT, "url", near, "image") == 0.8
    assert chat_similarity(CHAT, "url", near, "url") == 0.0
    assert chat_similarity(CHAT, "image", far, "image") == 0.0
    assert chat_similarity(None, "url", CHAT, "url") == 0.0


def test_hamming() -> None:
    assert hamming(0, 0b1011) == 3
    assert hamming(None, 1) is None
    assert hamming(-1, 0) == 64


def message(text: str, hour: int | None, minute: int | None, **extra: object) -> ChatMessage:
    return ChatMessage(text_norm=text, hour=hour, minute=minute, **extra)  # type: ignore[arg-type]


def test_client_messages_drop_seller_templates_and_untimed() -> None:
    kept = client_messages(
        (
            message("здравствуйте", 9, 0, side="seller"),
            message("брак", 9, 1, side="client"),
            message("без времени", None, None),
            message("", 9, 2),
            message("сторона неизвестна", 9, 3),
        )
    )
    assert [m.text_norm for m in kept] == ["брак", "сторона неизвестна"]


def test_matched_messages_pair_one_to_one_by_time_and_text() -> None:
    a = (message("пришел брак", 9, 1), message("верните деньги", 9, 5), message("жду", 9, 9))
    b = (message("верните деньги", 9, 5), message("пришел брак", 9, 1), message("жду", 9, 8))
    pairs = matched_messages(a, b, text_threshold=0.9)
    assert [(p.left, p.right) for p in pairs] == [(0, 1), (1, 0)]
    assert sum(p.text_len for p in pairs) == len("пришел брак") + len("верните деньги")


def test_matched_messages_respect_visible_dates() -> None:
    a = (message("пришел брак", 9, 1, day=1, month=10),)
    assert matched_messages(a, (message("пришел брак", 9, 1, day=2, month=10),), text_threshold=0.9) == ()
    dated = matched_messages(a, (message("пришел брак", 9, 1, day=1, month=10),), text_threshold=0.9)
    assert dated[0].dated
    assert not matched_messages(a, (message("пришел брак", 9, 1),), text_threshold=0.9)[0].dated


def test_matched_messages_accept_a_confused_digit() -> None:
    pairs = matched_messages((message("пришел брак", 6, 39),), (message("пришел брак", 8, 39),), text_threshold=0.9)
    assert pairs[0].score == 1.0
