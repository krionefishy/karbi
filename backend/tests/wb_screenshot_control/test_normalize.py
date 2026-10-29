from datetime import UTC, datetime

import pytest

from backend.modules.wb_screenshot_control.domain.normalize import (
    ScreenDateTime,
    buyer_hmac,
    chat_id_agreement,
    chat_id_from_image,
    chat_id_from_mirror,
    chat_id_from_url,
    datetime_agreement,
    has_truncation_tail,
    normalize_article,
    normalize_caption,
    normalize_text,
    parse_screen_datetime,
    resolve_screen_datetime,
    screen_datetime_from_numbers,
    text_agreement,
    time_agreement,
)

GUID = "ad70e889-d235-43bf-a634-b60a65bbce9b"
MSK = "Europe/Moscow"
# 23.10.2026 12:00 МСК
RECEIVED = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://seller.wildberries.ru/chat-with-clients?chatId={GUID}&chatType=1", GUID),
        (f"seller.wildberries.ru/chat-with-clients?chatId={GUID}", GUID),
        (f"https://seller.wildberries.ru/chat-with-clients/?chatType=1&chatId={GUID.upper()}", GUID),
        (f"https://SELLER.wildberries.ru/chat-with-clients?chatid={GUID}", GUID),
        (f"https://seller.wildberries.ru/chat-with-clients?chatId={GUID.replace('-', '')}", GUID),
        (f"https://seller.wildberries.ru/chat-with-clients?chatId=1:{GUID}", None),
        (f"https://evil.example/chat-with-clients?chatId={GUID}", None),
        (f"https://seller.wildberries.ru.evil.example/chat-with-clients?chatId={GUID}", None),
        (f"https://seller.wildberries.ru/feedbacks?chatId={GUID}", None),
        ("https://seller.wildberries.ru/chat-with-clients?chatId=ad70e889-d235", None),
        ("https://seller.wildberries.ru/chat-with-clients", None),
        ("", None),
        (None, None),
    ],
)
def test_chat_id_from_url(url: str | None, expected: str | None) -> None:
    assert chat_id_from_url(url) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (GUID, GUID),
        (GUID.upper(), GUID),
        ("ad70e889-d235-43bf-a634-\nb60a65bbce9b", GUID),
        ("ad70e889 - d235 - 43bf - a634 - b60a65bbce9b", GUID),
        (f"chatId={GUID}&chatType=1", GUID),
        (f"seller.wildberries.ru/chat-with-clients?chatId={GUID}&chatType=1", GUID),
        (f"1:{GUID}", GUID),
        # путаницы букв и цифр
        ("ad7Oe889-d235-43bf-a634-b60a65bbce9b", GUID),
        ("ad70e889-d235-43bf-a634-b6Oa65bbce9b", GUID),
        ("ad70e889-d235-43bf-a634-b60a65bbce9b".replace("43", "4З"), None),
        ("ad70e88I-d235-43bf-a634-b60a65bbce9b", "ad70e881-d235-43bf-a634-b60a65bbce9b"),
        ("ad70e88l-d235-43bf-a634-b60a65bbce9b", "ad70e881-d235-43bf-a634-b60a65bbce9b"),
        ("ad70e88S-d235-43bf-a634-b60a65bbce9b", "ad70e885-d235-43bf-a634-b60a65bbce9b"),
        # заглавная B среди строчных — прочитанная восьмёрка (c8ea → cBea, этап 0)
        ("ad70e889-d235-43bf-a634-b60a65bbcBea", "ad70e889-d235-43bf-a634-b60a65bbc8ea"),
        # GUID целиком заглавными: B там законная буква
        ("AD70E889-D235-43BF-A634-B60A65BBCE9B", GUID),
        # обрезан, лишний символ, не GUID
        ("ad70e889-d235-43bf-a634-b60a65bbce9", None),
        ("ad70e889-d235-43bf-a634-b60a65bbce9bb", None),
        ("ad70e889-d235-43bf-a634-b60a65bbce9g", None),
        ("", None),
        (None, None),
    ],
)
def test_chat_id_from_image(raw: str | None, expected: str | None) -> None:
    assert chat_id_from_image(raw) == expected


def test_the_mirror_prefix_is_not_part_of_our_chat_key() -> None:
    assert chat_id_from_mirror(f"1:{GUID}") == GUID
    assert chat_id_from_mirror(GUID.upper()) == GUID
    assert chat_id_from_mirror("1:broken") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("22 октября, 06:39", ScreenDateTime(day=22, month=10, hour=6, minute=39)),
        ("22 Октября в 6:39", ScreenDateTime(day=22, month=10, hour=6, minute=39)),
        ("1 мая", ScreenDateTime(day=1, month=5)),
        ("3 марта 2025, 10:00", ScreenDateTime(day=3, month=3, year=2025, hour=10, minute=0)),
        ("3 мар 2025 г., 10:00", ScreenDateTime(day=3, month=3, year=2025, hour=10, minute=0)),
        ("22.10.2026 06:39", ScreenDateTime(day=22, month=10, year=2026, hour=6, minute=39)),
        ("22.10.26, 06:39", ScreenDateTime(day=22, month=10, year=2026, hour=6, minute=39)),
        ("22.10", ScreenDateTime(day=22, month=10)),
        ("сегодня, 06:39", ScreenDateTime(hour=6, minute=39, days_ago=0)),
        ("Сегодня", ScreenDateTime(days_ago=0)),
        ("вчера в 23:59", ScreenDateTime(hour=23, minute=59, days_ago=1)),
        ("06:39", ScreenDateTime(hour=6, minute=39)),
        ("25:61", None),
        ("32 октября", None),
        ("22 непонятня", None),
        ("отзыв", None),
        ("", None),
        (None, None),
    ],
)
def test_screen_datetime_is_read_as_on_screen(text: str | None, expected: ScreenDateTime | None) -> None:
    parsed = parse_screen_datetime(text)
    if expected is None:
        assert parsed is None
    else:
        assert parsed == expected


@pytest.mark.parametrize(
    ("text", "received", "expected", "precision"),
    [
        # года нет — этот год, МСК → UTC (−3 ч)
        ("22 октября, 06:39", RECEIVED, datetime(2026, 10, 22, 3, 39, tzinfo=UTC), "minute"),
        # дата позже дня получения — прошлый год
        ("24 октября, 10:00", RECEIVED, datetime(2025, 10, 24, 7, 0, tzinfo=UTC), "minute"),
        # граница года: в январе «31 декабря» — прошлый декабрь
        (
            "31 декабря, 23:50",
            datetime(2027, 1, 1, 0, 30, tzinfo=UTC),
            datetime(2026, 12, 31, 20, 50, tzinfo=UTC),
            "minute",
        ),
        # 1 января 00:30 по UTC — уже 1 января по МСК, «1 января» — сегодняшняя дата
        ("1 января, 03:10", datetime(2027, 1, 1, 0, 30, tzinfo=UTC), datetime(2027, 1, 1, 0, 10, tzinfo=UTC), "minute"),
        # 31 декабря 22:00 UTC — по МСК уже 1 января: «31 декабря» не будущее
        ("31 декабря", datetime(2026, 12, 31, 22, 0, tzinfo=UTC), datetime(2026, 12, 30, 21, 0, tzinfo=UTC), "day"),
        # тот же день, время чуть позже получения (часы клиента/кабинета) — не прошлый год
        ("23 октября, 12:05", RECEIVED, datetime(2026, 10, 23, 9, 5, tzinfo=UTC), "minute"),
        ("22.10.2025 06:39", RECEIVED, datetime(2025, 10, 22, 3, 39, tzinfo=UTC), "minute"),
        ("сегодня, 06:39", RECEIVED, datetime(2026, 10, 23, 3, 39, tzinfo=UTC), "minute"),
        ("вчера, 23:59", RECEIVED, datetime(2026, 10, 22, 20, 59, tzinfo=UTC), "minute"),
        # «сегодня» по МСК, хотя по UTC ещё вчера
        (
            "сегодня, 01:00",
            datetime(2026, 10, 22, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 22, 22, 0, tzinfo=UTC),
            "minute",
        ),
        ("22 октября", RECEIVED, datetime(2026, 10, 21, 21, 0, tzinfo=UTC), "day"),
        # 29 февраля без года — последний високосный не позже получения
        ("29 февраля", RECEIVED, datetime(2024, 2, 28, 21, 0, tzinfo=UTC), "day"),
        # только время — ближайшее такое не позже получения
        ("11:30", RECEIVED, datetime(2026, 10, 23, 8, 30, tzinfo=UTC), "minute"),
        ("13:30", RECEIVED, datetime(2026, 10, 22, 10, 30, tzinfo=UTC), "minute"),
        ("31.02.2026", RECEIVED, None, None),
    ],
)
def test_screen_datetime_resolves_to_utc(
    text: str, received: datetime, expected: datetime | None, precision: str | None
) -> None:
    assert resolve_screen_datetime(parse_screen_datetime(text), received_at=received, timezone=MSK) == (
        expected,
        precision,
    )


def test_a_naive_received_at_is_taken_as_utc() -> None:
    value = parse_screen_datetime("22 октября, 06:39")
    naive = RECEIVED.replace(tzinfo=None)

    assert resolve_screen_datetime(value, received_at=naive, timezone=MSK)[0] == datetime(
        2026, 10, 22, 3, 39, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("numbers", "expected"),
    [
        ({"day": 22, "month": 10, "year": None, "hour": 6, "minute": 39}, ScreenDateTime(22, 10, None, 6, 39)),
        ({"day": None, "month": None, "year": None, "hour": 6, "minute": 39}, ScreenDateTime(hour=6, minute=39)),
        ({"day": 22, "month": 10, "year": 1999, "hour": None, "minute": None}, ScreenDateTime(22, 10)),
        ({"day": 22, "month": 13, "year": None, "hour": 6, "minute": 39}, None),
        ({"day": 22, "month": 10, "year": None, "hour": 24, "minute": 0}, None),
        ({"day": 22, "month": None, "year": None, "hour": 6, "minute": 39}, None),
        ({"day": None, "month": None, "year": None, "hour": None, "minute": None}, None),
    ],
)
def test_numbers_from_the_model_are_range_checked(numbers: dict, expected: ScreenDateTime | None) -> None:
    assert screen_datetime_from_numbers(**numbers) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Отличный товар!", "отличный товар"),
        ("  «Ёлка»  —  супер!!! ", "елка супер"),
        ("Всё ок 👍🏻 спасибо", "все ок спасибо"),
        ("семья‍👨‍👩‍👧 рада", "семья рада"),
        ("ＦＵＬＬＷＩＤＴＨ ＡＢＣ １２３", "fullwidth abc 123"),
        ("Размер маломерит… Показать полностью", "размер маломерит"),
        ("Размер маломерит... свернуть", "размер маломерит"),
        ("Размер маломерит…", "размер маломерит"),
        ("Не смог развернуть", "не смог развернуть"),
        ("Straße", "strasse"),
        ("сло\u200bво при\u00adшло\ufeff", "слово пришло"),  # невидимое внутри слова не рвёт его
        ("", ""),
        (None, ""),
    ],
)
def test_text_normalization(text: str | None, expected: str) -> None:
    assert normalize_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Ткань тонкая…", True), ("Ткань тонкая...", True), ("Ткань Показать полностью", True), ("Ткань", False)],
)
def test_truncation_tail_is_detected(text: str, expected: bool) -> None:
    assert has_truncation_tail(text) is expected


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("полож", "positive"),
        ("Положительный", "positive"),
        ("Доп.", "amended"),
        ("доп", "amended"),
        ("УДАЛЁН", "deleted"),
        ("удалил", "deleted"),
        ("пoлoж", "positive"),  # латинские o
        ("ДOП", "amended"),  # латинская O
        ("yдaл", "deleted"),  # латинские y, a
        ("полож, полож!", "positive"),
        ("полож доп", None),
        ("удален или доп", None),
        ("отзыв", None),
        ("", None),
        (None, None),
    ],
)
def test_caption_normalization(caption: str | None, expected: str | None) -> None:
    assert normalize_caption(caption) == expected


def test_buyer_name_becomes_a_short_keyed_hash() -> None:
    first = buyer_hmac("Анна  Петрова", "key")
    assert first is not None and len(first) == 16
    assert buyer_hmac("анна петрова", "key") == first
    assert buyer_hmac("АННА ПЁТРОВА", "key") == buyer_hmac("анна петрова", "key")
    assert buyer_hmac("Анна Петрова", "other-key") != first
    assert buyer_hmac("Мария", "key") != first


@pytest.mark.parametrize(("name", "key"), [("Анна", ""), (None, "key"), ("   ", "key")])
def test_without_a_key_or_name_there_is_no_hash(name: str | None, key: str) -> None:
    assert buyer_hmac(name, key) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123456789", "123456789"),
        ("123 456 789", "123456789"),
        ("Артикул WB: 123456789", "123456789"),
        ("арт. ab-12 c", "AB-12C"),
        ("Артикул продавца: футболка-01", "ФУТБОЛКА-01"),
        ("", None),
        (None, None),
    ],
)
def test_article_normalization(value: str | None, expected: str | None) -> None:
    assert normalize_article(value) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (GUID, GUID, 1.0),
        (GUID, GUID.upper(), 1.0),
        (GUID, "ad7Oe889-d235-43bf-a634-b60a65bbce9b", 1.0),
        (GUID, GUID[:-1] + "c", 0.8),
        (GUID, GUID[:-2] + "00", 0.8),
        (GUID, GUID + "b", 0.8),
        (GUID, GUID[:-3] + "000", 0.0),
        (GUID, "11111111-2222-3333-4444-555555555555", 0.0),
        (GUID, None, None),
        (None, GUID, None),
    ],
)
def test_chat_id_agreement(a: str | None, b: str | None, expected: float | None) -> None:
    assert chat_id_agreement(a, b) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0639", "0639", 1.0),
        ("0639", "0839", 0.9),  # 6/8
        ("0639", "0689", 0.9),  # 3/8
        ("1030", "1038", 0.9),  # 0/8
        ("1120", "1720", 0.9),  # 1/7
        ("0655", "0656", 0.9),  # 5/6
        ("0639", "0739", 0.0),  # 6/7 — не путаница
        ("0639", "0838", 0.0),  # две цифры
        ("22100639", "22100839", 0.9),
        ("0639", "220639", 0.0),
        ("0639", None, None),
    ],
)
def test_time_agreement(a: str, b: str | None, expected: float | None) -> None:
    assert time_agreement(a, b) == expected


def test_datetime_agreement_compares_what_both_readings_show() -> None:
    full = ScreenDateTime(day=22, month=10, hour=6, minute=39)
    assert datetime_agreement(full, ScreenDateTime(day=22, month=10, hour=8, minute=39)) == 0.9
    assert datetime_agreement(full, ScreenDateTime(hour=6, minute=39)) == 1.0
    assert datetime_agreement(ScreenDateTime(hour=6, minute=39, days_ago=0), full) == 1.0
    assert (
        datetime_agreement(ScreenDateTime(days_ago=0, hour=1, minute=0), ScreenDateTime(days_ago=1, hour=1, minute=0))
        == 0.0
    )
    assert datetime_agreement(full, ScreenDateTime(day=22, month=10)) == 1.0
    assert datetime_agreement(full, None) is None


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("Отличный товар", "отличный товар!", 1.0),
        ("абвгд", "абвгж", 0.8),
        ("абвгд", "", 0.0),
        ("", "", 1.0),
        ("абв", None, None),
    ],
)
def test_text_agreement(a: str, b: str | None, expected: float | None) -> None:
    assert text_agreement(a, b) == expected
