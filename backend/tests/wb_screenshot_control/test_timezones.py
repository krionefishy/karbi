"""Пояс менеджера: список поясов, время на экране в разных поясах, пересчёт полей файла под сабмишен
и решение по скринам менеджеров из разных поясов. Без БД."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.modules.wb_screenshot_control.domain.decide import decide
from backend.modules.wb_screenshot_control.domain.normalize import (
    ScreenDateTime,
    parse_screen_datetime,
    resolve_screen_datetime,
)
from backend.modules.wb_screenshot_control.domain.pipeline_types import ChatMessage, ExtractedFields
from backend.modules.wb_screenshot_control.domain.similarity import TIME_EXACT, TIME_SAME_DAY, time_similarity
from backend.modules.wb_screenshot_control.domain.timezones import (
    DEFAULT_TIMEZONE,
    ZONE_IDS,
    is_known_zone,
    messages_in_zone,
    offset_label,
    rebase_fields,
    zone_options,
)
from backend.tests.wb_screenshot_control.test_decide import (
    CFG,
    CHAT_1,
    TEXT,
    USER_B,
    desktop,
    make_candidate,
    make_input,
    mobile,
    msg,
)

MSK, EKB, VVO = "Europe/Moscow", "Asia/Yekaterinburg", "Asia/Vladivostok"


# --- список поясов ---


def test_the_zone_list_is_closed_and_labelled_in_russian() -> None:
    options = zone_options(datetime(2026, 10, 30, tzinfo=UTC))
    labels = {item.id: item.label for item in options}
    assert len(options) == 13 and DEFAULT_TIMEZONE == MSK
    assert labels[MSK] == "Москва, UTC+3"
    assert labels["Europe/Kaliningrad"] == "Калининград, UTC+2"
    assert labels[EKB] == "Екатеринбург, UTC+5"
    assert labels["Asia/Kamchatka"] == "Камчатка, UTC+12"
    assert labels["Europe/Minsk"] == "Минск, UTC+3"
    assert labels["Asia/Almaty"].startswith("Алматы, UTC+")
    assert [item.utc_offset_minutes for item in options][:4] == [120, 180, 240, 300]
    for zone in ZONE_IDS:
        ZoneInfo(zone)  # каждое имя известно базе поясов
    assert is_known_zone(VVO) and not is_known_zone("Europe/Kyiv") and not is_known_zone("Moscow")
    assert not is_known_zone(None) and not is_known_zone("")
    assert offset_label(-90) == "UTC−1:30" and offset_label(0) == "UTC+0"


# --- время на экране в поясе менеджера ---


@pytest.mark.parametrize(
    ("text", "received", "zone", "expected", "precision"),
    [
        # тот же экран «22 октября, 06:39» — разные моменты UTC
        ("22 октября, 06:39", datetime(2026, 10, 23, 9, 0, tzinfo=UTC), MSK, datetime(2026, 10, 22, 3, 39), "minute"),
        ("22 октября, 06:39", datetime(2026, 10, 23, 9, 0, tzinfo=UTC), EKB, datetime(2026, 10, 22, 1, 39), "minute"),
        ("22 октября, 06:39", datetime(2026, 10, 23, 9, 0, tzinfo=UTC), VVO, datetime(2026, 10, 21, 20, 39), "minute"),
        # «сегодня» во Владивостоке уже наступило, в Москве ещё вчера: 23.10 20:00 UTC = 24.10 06:00 VVO
        ("сегодня, 05:10", datetime(2026, 10, 23, 20, 0, tzinfo=UTC), VVO, datetime(2026, 10, 23, 19, 10), "minute"),
        ("сегодня, 05:10", datetime(2026, 10, 23, 20, 0, tzinfo=UTC), MSK, datetime(2026, 10, 23, 2, 10), "minute"),
        ("вчера", datetime(2026, 10, 23, 20, 0, tzinfo=UTC), VVO, datetime(2026, 10, 22, 14, 0), "day"),
        ("вчера", datetime(2026, 10, 23, 20, 0, tzinfo=UTC), MSK, datetime(2026, 10, 21, 21, 0), "day"),
        # телефонное время без даты: ближайшее такое время не позже получения в поясе менеджера
        ("23:50", datetime(2026, 10, 23, 19, 0, tzinfo=UTC), EKB, datetime(2026, 10, 23, 18, 50), "minute"),
        ("23:50", datetime(2026, 10, 23, 19, 0, tzinfo=UTC), MSK, datetime(2026, 10, 22, 20, 50), "minute"),
        # конец года: 31 декабря, полученное 1 января по Владивостоку, — прошлый год
        ("31 декабря, 23:30", datetime(2026, 12, 31, 15, 0, tzinfo=UTC), VVO, datetime(2026, 12, 31, 13, 30), "minute"),
        # в Москве 1 января ещё не наступило, «1 января» — прошлогоднее
        ("1 января, 00:30", datetime(2026, 12, 31, 15, 0, tzinfo=UTC), VVO, datetime(2026, 12, 31, 14, 30), "minute"),
        ("1 января, 00:30", datetime(2026, 12, 31, 15, 0, tzinfo=UTC), MSK, datetime(2025, 12, 31, 21, 30), "minute"),
    ],
)
def test_the_screen_time_is_resolved_in_the_managers_zone(
    text: str, received: datetime, zone: str, expected: datetime, precision: str
) -> None:
    moment, found = resolve_screen_datetime(parse_screen_datetime(text), received_at=received, timezone=zone)
    assert moment == expected.replace(tzinfo=UTC) and found == precision


# --- поля файла → поля сабмишена ---


def _fields(zone: str, received: datetime) -> ExtractedFields:
    screen = ScreenDateTime(day=22, month=10, hour=6, minute=39)
    review_at, precision = resolve_screen_datetime(screen, received_at=received, timezone=zone)
    return replace(
        desktop(review_at=review_at, review_time_precision=precision),
        review_screen=screen,
        timezone=zone,
        messages=(ChatMessage("вчерашняя реплика покупателя", hour=21, minute=0, day=22, month=10, days_ago=1),),
    )


def test_rebasing_gives_the_same_file_its_own_moment_for_each_uploader() -> None:
    received = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
    first = _fields(MSK, received)

    same = rebase_fields(first, timezone=MSK, received_at=received)
    assert same == first  # тот же пояс и момент приёма — ничего не меняется

    other = rebase_fields(first, timezone=EKB, received_at=received)
    assert other.review_at == datetime(2026, 10, 22, 1, 39, tzinfo=UTC) and other.timezone == EKB
    assert other.review_screen == first.review_screen and other.text_norm == first.text_norm

    late = rebase_fields(first, timezone=VVO, received_at=datetime(2026, 10, 23, 20, 0, tzinfo=UTC))
    assert (late.messages[0].day, late.messages[0].month) == (23, 10), "«вчера» по Владивостоку уже 23.10"


def test_old_extractions_without_the_screen_time_stay_as_they_were() -> None:
    legacy = desktop()  # распознано до пояса менеджера: ни review_screen, ни timezone
    assert rebase_fields(legacy, timezone=VVO, received_at=datetime(2026, 10, 23, tzinfo=UTC)) is legacy


def test_message_times_are_shifted_between_zones_across_midnight() -> None:
    at = datetime(2026, 10, 23, tzinfo=UTC)
    messages = (msg("поздняя реплика", 23, 30, day=22, month=10), msg("без даты", 1, 15))
    shifted = messages_in_zone(messages, source=MSK, target=EKB, at=at)
    assert (shifted[0].hour, shifted[0].minute, shifted[0].day, shifted[0].month) == (1, 30, 23, 10)
    assert (shifted[1].hour, shifted[1].minute, shifted[1].day) == (3, 15, None)
    back = messages_in_zone(shifted, source=EKB, target=MSK, at=at)
    assert back == messages
    assert messages_in_zone(messages, source=MSK, target="Europe/Minsk", at=at) == messages


def test_a_day_only_date_is_compared_in_the_zone_it_was_read_in() -> None:
    msk_day = datetime(2026, 10, 21, 21, 0, tzinfo=UTC)  # 22.10 00:00 по Москве
    ekb_day = datetime(2026, 10, 21, 19, 0, tzinfo=UTC)  # 22.10 00:00 по Екатеринбургу
    assert time_similarity(msk_day, "day", ekb_day, "day", ZoneInfo(MSK), b_tz=ZoneInfo(EKB)) == TIME_SAME_DAY
    # без пояса кандидата полночь Екатеринбурга по Москве — ещё 21.10
    assert time_similarity(msk_day, "day", ekb_day, "day", ZoneInfo(MSK)) == 0.0
    minute = datetime(2026, 10, 22, 3, 39, tzinfo=UTC)
    assert time_similarity(minute, "minute", ekb_day, "day", ZoneInfo(MSK), b_tz=ZoneInfo(EKB)) == TIME_SAME_DAY
    assert time_similarity(minute, "minute", minute, "minute", ZoneInfo(EKB), b_tz=ZoneInfo(MSK)) == TIME_EXACT


# --- решение по скринам менеджеров из разных поясов ---

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
REVIEW_UTC = datetime(2026, 10, 20, 3, 39, tzinfo=UTC)  # 06:39 МСК = 08:39 ЕКБ


def _zoned(zone: str, review_at: datetime = REVIEW_UTC) -> ExtractedFields:
    local = review_at.astimezone(ZoneInfo(zone))
    screen = ScreenDateTime(day=local.day, month=local.month, hour=local.hour, minute=local.minute)
    return replace(desktop(review_at=review_at), review_screen=screen, timezone=zone)


def test_the_same_review_from_managers_in_different_zones_is_a_duplicate() -> None:
    original = make_candidate(1, _zoned(MSK), user_id=USER_B)
    decision = decide(make_input(_zoned(EKB)), [original], CFG)
    assert decision.verdict == "duplicate" and decision.duplicate_of == original.submission_id
    assert {"case_key", "cross_user_duplicate"} <= {reason.code for reason in decision.reasons}


def test_a_wrong_zone_shifts_the_time_by_whole_hours_and_goes_to_review() -> None:
    # Менеджер в Омске оставил Москву: 09:39 с экрана прочитано как 09:39 МСК — на 3 часа позже.
    original = make_candidate(1, _zoned("Asia/Omsk"), user_id=USER_B)
    wrong = _zoned(MSK, REVIEW_UTC + timedelta(hours=3))
    decision = decide(make_input(wrong), [original], CFG)
    assert decision.verdict == "needs_review"
    reasons = {reason.code: reason for reason in decision.reasons}
    assert "same_chat_same_text_other_time" in reasons
    assert reasons["same_chat_same_text_other_time"].detail["other_submission_id"] == str(original.submission_id)
    assert reasons["same_chat_same_text_other_time"].severity == "high"


def test_a_wrong_zone_that_looks_like_a_misread_digit_is_still_a_duplicate() -> None:
    # Сдвиг на 2 часа 06:39 → 08:39 совпадает с путаницей цифр 6/8: тот же чат и текст — дубль, как и должно быть.
    original = make_candidate(1, _zoned(EKB), user_id=USER_B)
    wrong = _zoned(MSK, REVIEW_UTC + timedelta(hours=2))
    assert decide(make_input(wrong), [original], CFG).verdict == "duplicate"


def test_a_phone_conversation_matches_across_zones_by_message_times() -> None:
    history_msk = (
        msg("здравствуйте пришел не тот цвет", 10, 1, day=20, month=10),
        msg("фото прикрепила посмотрите пожалуйста", 10, 7, day=20, month=10),
        msg("когда вернете деньги за заказ", 10, 30, day=20, month=10),
    )
    history_ekb = messages_in_zone(history_msk, source=MSK, target=EKB, at=NOW)
    assert history_ekb[0].hour == 12
    original = make_candidate(1, replace(mobile(history_msk), timezone=MSK), user_id=USER_B)
    copy = replace(mobile(history_ekb), timezone=EKB, review_at=original.fields.review_at)  # type: ignore[union-attr]
    decision = decide(make_input(copy), [original], CFG)
    assert decision.verdict == "duplicate"
    assert {"messages_overlap", "case_key"} & {reason.code for reason in decision.reasons}

    # без перевода в один пояс реплики разошлись бы на 2 часа и совпадения не было
    unshifted = replace(mobile(history_ekb), review_at=original.fields.review_at)  # type: ignore[union-attr]
    plain = decide(make_input(replace(unshifted, timezone=MSK, review_at=None)), [original], CFG)
    assert plain.verdict != "duplicate"


def test_the_plausibility_of_a_day_only_date_uses_the_uploaders_zone() -> None:
    # 22.10 по Владивостоку уже наступило (09:00 UTC = 19:00 VVO), а по Москве ещё 22.10 — оба не в будущем;
    # «23.10» по Владивостоку при приёме 22.10 15:00 UTC (23.10 01:00 VVO) — тоже не будущее.
    received = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
    day = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)  # 23.10 00:00 VVO
    fields = replace(
        desktop(chat_id=CHAT_1, text_norm=TEXT, review_at=day, review_time_precision="day"),
        timezone=VVO,
        review_screen=ScreenDateTime(day=23, month=10),
    )
    codes = {reason.code for reason in decide(make_input(fields, received_at=received), [], CFG).reasons}
    assert "review_date_implausible" not in codes
    in_moscow = replace(fields, timezone=None)  # тот же момент по Москве — 22.10 17:00, «день» 22.10 — тоже ок
    codes = {reason.code for reason in decide(make_input(in_moscow, received_at=received), [], CFG).reasons}
    assert "review_date_implausible" not in codes
    future = replace(fields, review_at=day + timedelta(days=1))
    codes = {reason.code for reason in decide(make_input(future, received_at=received), [], CFG).reasons}
    assert "review_date_implausible" in codes
