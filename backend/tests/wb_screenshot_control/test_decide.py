"""Решение по скриншоту: коллизии 7.6, дыры из ревью, каждая строка таблицы 8.2 и границы порогов.

Фикстуры синтетические: поля уже нормализованы, как их отдаёт распознавание.
"""

import copy
import itertools
import random
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.modules.wb_screenshot_control.domain.decide import decide
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ChatMessage,
    ClientHints,
    Decision,
    DecisionInput,
    ExtractedFields,
    ExtractionResult,
    QualityFlags,
)
from backend.shared.settings.config import ScreenshotControlConfig

CFG = ScreenshotControlConfig(audit_percent=0.0)
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
REVIEW_AT = datetime(2026, 10, 20, 3, 39, tzinfo=UTC)  # 06:39 по Москве
USER_A = uuid.UUID(int=0xA)
USER_B = uuid.UUID(int=0xB)
CHAT_1 = "0b8f3c2a-1d4e-4f6a-9b7c-2e5d8a1f3c40"
CHAT_2 = "7d1e9a4b-5c2f-4e8d-a3b6-9f0c1e2d7a58"
TEXT = "товар пришел с браком упаковка порвана продавец не отвечает на вопросы"
OTHER_TEXT = "размер не соответствует таблице вернула через пункт выдачи деньги ждем"
INPUT_ID = uuid.UUID(int=1000)


def desktop(**changes: Any) -> ExtractedFields:
    base = ExtractedFields(
        layout="desktop_window",
        is_wb_seller_chat=True,
        chat_id=CHAT_1,
        chat_id_source="url",
        review_at=REVIEW_AT,
        review_time_precision="minute",
        stars=1,
        text_norm=TEXT,
    )
    return replace(base, **changes)


def msg(text: str, hour: int, minute: int, day: int | None = None, month: int | None = None, side: str = "client"):
    return ChatMessage(text_norm=text, hour=hour, minute=minute, day=day, month=month, side=side)  # type: ignore[arg-type]


HISTORY = (
    msg("здравствуйте пришел не тот цвет", 10, 1),
    msg("добрый день пришлите фото", 10, 5, side="seller"),
    msg("фото прикрепила посмотрите пожалуйста", 10, 7),
    msg("когда вернете деньги за заказ", 10, 30),
)


def mobile(messages: tuple[ChatMessage, ...] = HISTORY, **changes: Any) -> ExtractedFields:
    client = [m for m in messages if m.side != "seller"]
    last = client[-1] if client else None
    base = ExtractedFields(
        layout="mobile_app",
        is_wb_seller_chat=True,
        review_at=REVIEW_AT.replace(hour=(last.hour or 0) - 3, minute=last.minute or 0) if last else None,
        review_time_precision="minute" if last else None,
        text_norm=last.text_norm if last else None,
        messages=messages,
    )
    return replace(base, **changes)


def make_input(fields: ExtractedFields | None = None, **changes: Any) -> DecisionInput:
    extraction = (
        ExtractionResult(status="ok", fields=fields) if fields is not None else ExtractionResult(status="failed")
    )
    base = DecisionInput(
        submission_id=INPUT_ID,
        user_id=USER_A,
        channel="web",
        received_at=NOW,
        outcome="positive",
        file_sha256="f" * 64,
        dhash=None,
        extraction=extraction,
    )
    return replace(base, **changes)


def make_candidate(n: int, fields: ExtractedFields | None, **changes: Any) -> Candidate:
    base = Candidate(
        submission_id=uuid.UUID(int=n),
        user_id=USER_A,
        received_at=NOW - timedelta(hours=n),
        verdict="accepted",
        outcome="positive",
        file_sha256=f"{n:064x}",
        fields=fields,
    )
    return replace(base, **changes)


def codes(decision: Decision) -> list[str]:
    return [reason.code for reason in decision.reasons]


def run(fields: ExtractedFields | None, candidates: list[Candidate] = (), **changes: Any) -> Decision:  # type: ignore[assignment]
    return decide(make_input(fields, **changes), candidates, CFG)


# --- 7.6: двенадцать коллизий ---

COLLISIONS: list[tuple[str, ExtractedFields, list[Candidate], dict[str, Any], str, str | None]] = [
    (
        "1 один покупатель два отзыва: тот же чат, другое время и текст",
        desktop(text_norm=OTHER_TEXT, review_at=REVIEW_AT + timedelta(days=1, hours=2)),
        [make_candidate(1, desktop())],
        {},
        "accepted",
        None,
    ),
    (
        "2 пересохранён: sha другой, хеш близок, ключ совпал",
        desktop(),
        [make_candidate(1, desktop(), dhash=0b1011)],
        {"dhash": 0b1000},
        "duplicate",
        "case_key",
    ),
    (
        "3 первый свёрнут, второй развёрнут",
        desktop(text_norm=TEXT + " и вообще больше ничего здесь не закажу никогда"),
        [make_candidate(1, desktop(text_truncated=True))],
        {},
        "duplicate",
        "case_key",
    ),
    (
        "4 06:39 прочитано как 08:39",
        desktop(review_at=REVIEW_AT + timedelta(hours=2)),
        [make_candidate(1, desktop())],
        {},
        "duplicate",
        "case_key",
    ),
    (
        "5 два покупателя в одну минуту написали «Ужасно»",
        mobile((msg("ужасно", 10, 30),)),
        [make_candidate(1, mobile((msg("ужасно", 10, 30),)))],
        {},
        "needs_review",
        "weak_key",
    ),
    (
        "6 новый отзыв с тем же текстом после удаления",
        desktop(review_at=REVIEW_AT + timedelta(days=1)),
        [make_candidate(1, desktop())],
        {},
        "needs_review",
        "same_chat_same_text_other_time",
    ),
    (
        "7 менеджер Б прислал кейс менеджера А",
        desktop(),
        [make_candidate(1, desktop())],
        {"user_id": USER_B},
        "duplicate",
        "cross_user_duplicate",
    ),
    (
        "8 тот же кейс другой подписью тем же менеджером",
        desktop(),
        [make_candidate(1, desktop())],
        {"outcome": "amended"},
        "needs_review",
        "caption_conflict",
    ),
    (
        "9 старый кейс с отредактированным временем",
        desktop(review_at=REVIEW_AT - timedelta(hours=5, minutes=13)),
        [make_candidate(1, desktop())],
        {},
        "needs_review",
        "same_chat_same_text_other_time",
    ),
    (
        "10 сжатое фото из Telegram того же кейса, chatId прочитан с картинки с ошибкой",
        desktop(chat_id=CHAT_1[:-1] + "1", chat_id_source="image"),
        [make_candidate(1, desktop(), dhash=0xFF00)],
        {"channel": "telegram", "dhash": 0xFF01},
        "duplicate",
        "case_key",
    ),
    (
        "11 десктоп «отзыв» и телефон «чат» одного кейса не ловятся",
        mobile(),
        [make_candidate(1, desktop())],
        {},
        "accepted",
        None,
    ),
    (
        "12 шаблонный текст у разных покупателей в разных чатах",
        desktop(chat_id=CHAT_2, text_norm="не соответствует описанию", review_at=REVIEW_AT + timedelta(hours=7)),
        [make_candidate(1, desktop(text_norm="не соответствует описанию"))],
        {},
        "accepted",
        None,
    ),
]


@pytest.mark.parametrize(
    ("fields", "candidates", "changes", "verdict", "code"),
    [case[1:] for case in COLLISIONS],
    ids=[case[0] for case in COLLISIONS],
)
def test_collisions_from_the_design(
    fields: ExtractedFields, candidates: list[Candidate], changes: dict[str, Any], verdict: str, code: str | None
) -> None:
    decision = run(fields, candidates, **changes)
    assert decision.verdict == verdict
    if code is not None:
        assert code in codes(decision)
    if verdict == "duplicate":
        assert decision.duplicate_of == candidates[0].submission_id
    else:
        assert decision.duplicate_of is None


def test_resaved_screenshot_also_records_the_near_image_match() -> None:
    decision = run(desktop(), [make_candidate(1, desktop(), dhash=0b1011)], dhash=0b1000)
    assert {match.kind for match in decision.matches} == {"case_key", "near_image"}


def test_misread_time_is_marked_in_match_details() -> None:
    decision = run(desktop(review_at=REVIEW_AT + timedelta(hours=2)), [make_candidate(1, desktop())])
    assert decision.matches[0].details["s_time"] == 0.9


def test_other_manager_with_other_caption_is_still_a_duplicate() -> None:
    decision = run(desktop(), [make_candidate(1, desktop())], user_id=USER_B, outcome="deleted")
    assert decision.verdict == "duplicate"
    assert {"case_key", "cross_user_duplicate", "caption_conflict"} <= set(codes(decision))
    severity = {reason.code: reason.severity for reason in decision.reasons}
    assert severity["cross_user_duplicate"] == "high"


# --- дыры из ревью ---


def test_forged_chat_id_with_same_text_and_minute_goes_to_review() -> None:
    decision = run(
        desktop(chat_id=CHAT_2),
        [make_candidate(1, desktop())],
        channel="extension",
        hints=ClientHints(url=f"https://seller.wildberries.ru/chat-with-clients?chatId={CHAT_2}"),
    )
    assert decision.verdict == "needs_review"
    assert "same_text_time_other_chat" in codes(decision)


def test_forged_chat_id_with_overlapping_messages_goes_to_review() -> None:
    history = (msg("пришел не тот цвет совсем другой", 9, 1), msg("верните деньги за этот заказ", 9, 4))
    decision = run(
        desktop(chat_id=CHAT_2, text_norm=OTHER_TEXT, review_at=REVIEW_AT + timedelta(hours=1), messages=history),
        [make_candidate(1, desktop(messages=history))],
    )
    assert decision.verdict == "needs_review"
    assert "same_text_time_other_chat" in codes(decision)


DOM_EDITED = "товар пришел с браком коробка порвана продавец не отвечает на вопросы совсем"


@pytest.mark.parametrize("held_by", ["accepted", "duplicate", "needs_review", None])
def test_dom_edit_same_chat_slightly_other_text_other_time(held_by: str | None) -> None:
    """Оригинал на проверке тоже держит кейс: иначе после его одобрения засчитались бы оба."""
    decision = run(
        desktop(text_norm=DOM_EDITED, review_at=REVIEW_AT + timedelta(days=3)),
        [make_candidate(1, desktop(), verdict=held_by)],
    )
    assert decision.verdict == "needs_review"
    assert "same_chat_similar_text" in codes(decision)


LONG_TEXT = "товар пришел с браком упаковка порвана продавец не отвечает на вопросы уже третий день подряд"
LONG_EDITED = "товар пришел с дефектом коробка порвана продавец не отвечает на вопросы уже четвертый день подряд"


@pytest.mark.parametrize("user", [USER_A, USER_B])
@pytest.mark.parametrize(
    ("label", "changes"),
    [
        ("другой чат и время", {"chat_id": CHAT_2, "review_at": REVIEW_AT + timedelta(hours=3)}),
        ("другой чат, три слова, та же минута", {"chat_id": CHAT_2, "text_norm": LONG_EDITED}),
        (
            "другой чат, три слова и время",
            {"chat_id": CHAT_2, "text_norm": LONG_EDITED, "review_at": REVIEW_AT + timedelta(days=1)},
        ),
        ("чат не прочитан, время другое", {"chat_id": None, "review_at": REVIEW_AT + timedelta(hours=5)}),
    ],
)
def test_a_swapped_chat_with_a_second_edit_goes_to_review(label: str, changes: dict[str, Any], user: uuid.UUID) -> None:
    original = make_candidate(1, desktop(text_norm=LONG_TEXT))
    decision = run(desktop(**{"text_norm": LONG_TEXT, **changes}), [original], user_id=user)
    assert decision.verdict == "needs_review", (label, codes(decision))
    assert "same_text_other_time" in codes(decision) or "same_text_time_other_chat" in codes(decision)
    assert ("cross_user_duplicate" in codes(decision)) == (user == USER_B)


def test_the_same_text_in_the_same_minute_of_another_chat_keeps_its_own_reason() -> None:
    decision = run(desktop(chat_id=CHAT_2, text_norm=LONG_TEXT), [make_candidate(1, desktop(text_norm=LONG_TEXT))])
    assert codes(decision)[0] == "same_text_time_other_chat"


def test_a_short_text_in_another_chat_at_another_time_is_a_new_case() -> None:
    short = "товар пришел с браком коробка порвана"  # длиннее min_text_len, короче двух
    decision = run(
        desktop(chat_id=CHAT_2, text_norm=short, review_at=REVIEW_AT + timedelta(days=2)),
        [make_candidate(1, desktop(text_norm=short))],
    )
    assert decision.verdict == "accepted", codes(decision)


def test_a_different_long_text_in_another_chat_is_a_new_case() -> None:
    decision = run(
        desktop(chat_id=CHAT_2, text_norm=OTHER_TEXT + " и еще раз спасибо за быстрый возврат денег"),
        [make_candidate(1, desktop(text_norm=LONG_TEXT))],
    )
    assert decision.verdict == "accepted", codes(decision)


def test_mobile_dialog_continuation_is_the_same_case() -> None:
    continued = (*HISTORY[1:], msg("жду ответа уже третий день", 18, 2))
    decision = run(mobile(continued), [make_candidate(1, mobile())])
    assert decision.verdict == "duplicate"
    assert "messages_overlap" in codes(decision)
    assert decision.duplicate_of == uuid.UUID(int=1)


def test_short_replies_with_a_long_history_are_not_a_weak_key() -> None:
    chatty = (msg("здравствуйте", 9, 1), msg("брак", 9, 2), msg("шов разошелся", 9, 3), msg("верните", 9, 4))
    assert run(mobile(chatty)).verdict == "accepted"


def test_a_single_short_reply_is_a_weak_key() -> None:
    decision = run(mobile((msg("ужасно", 9, 1),)))
    assert decision.verdict == "needs_review"
    assert codes(decision) == ["weak_key"]


LONG_REPLY = (msg("когда вернете деньги за бракованный заказ", 10, 30),)


def test_time_without_date_alone_is_only_a_possible_duplicate() -> None:
    decision = run(mobile(LONG_REPLY), [make_candidate(1, mobile(LONG_REPLY))])
    assert decision.verdict == "needs_review"
    assert "possible_duplicate" in codes(decision)


def test_time_without_date_with_the_same_buyer_is_a_duplicate() -> None:
    decision = run(mobile(LONG_REPLY, buyer_hmac="h1"), [make_candidate(1, mobile(LONG_REPLY, buyer_hmac="h1"))])
    assert decision.verdict == "duplicate"
    assert "case_key" in codes(decision)


def test_time_with_date_is_a_duplicate_without_weak_signals() -> None:
    dated = (msg("когда вернете деньги за бракованный заказ", 10, 30, day=20, month=10),)
    decision = run(mobile(dated), [make_candidate(1, mobile(dated))])
    assert decision.verdict == "duplicate"


def test_other_buyer_on_the_phone_is_another_case() -> None:
    decision = run(mobile(LONG_REPLY, buyer_hmac="h1"), [make_candidate(1, mobile(LONG_REPLY, buyer_hmac="h2"))])
    assert decision.verdict == "accepted"


def test_other_article_on_the_phone_is_another_case() -> None:
    decision = run(mobile(LONG_REPLY, article_norm="1"), [make_candidate(1, mobile(LONG_REPLY, article_norm="2"))])
    assert decision.verdict == "accepted"


def test_template_prefix_of_a_truncated_text_does_not_match_another_review() -> None:
    template = "не соответствует описанию"
    decision = run(
        desktop(
            text_norm=template + " цвет другой и размер маломерит сильно", review_at=REVIEW_AT + timedelta(hours=3)
        ),
        [make_candidate(1, desktop(text_norm=template, text_truncated=True))],
    )
    assert decision.verdict == "accepted"


def test_mobile_same_text_other_time() -> None:
    later = (msg("когда вернете деньги за бракованный заказ", 15, 45, day=20, month=10),)
    dated = (msg("когда вернете деньги за бракованный заказ", 10, 30, day=20, month=10),)
    decision = run(mobile(later), [make_candidate(1, mobile(dated))])
    assert decision.verdict == "needs_review"
    assert "same_text_other_time" in codes(decision)


def test_day_only_precision_with_same_text_is_a_possible_duplicate() -> None:
    decision = run(
        desktop(chat_id=None, review_time_precision="day"),
        [make_candidate(1, desktop(chat_id=None))],
    )
    assert decision.verdict == "needs_review"
    assert "possible_duplicate" in codes(decision)


# --- таблица 8.2, строка за строкой ---


def test_row_1_no_outcome_rejects_first() -> None:
    decision = run(desktop(), [make_candidate(1, desktop())], outcome=None)
    assert decision.verdict == "rejected"
    assert codes(decision)[0] == "no_outcome"


def test_row_2_exact_file() -> None:
    decision = run(desktop(), [make_candidate(1, None, file_sha256="f" * 64)])
    assert decision.verdict == "duplicate"
    assert codes(decision) == ["exact_file"]
    assert decision.matches[0].kind == "exact_file"


def test_row_2_exact_file_of_another_user() -> None:
    decision = run(desktop(), [make_candidate(1, None, file_sha256="f" * 64, user_id=USER_B)])
    assert codes(decision) == ["exact_file", "cross_user_duplicate"]


def test_row_2_rejected_files_do_not_hold_the_case() -> None:
    decision = run(desktop(), [make_candidate(1, desktop(), file_sha256="f" * 64, verdict="rejected")])
    assert decision.verdict == "accepted"


def test_row_2_wins_over_not_a_wb_screenshot() -> None:
    decision = run(desktop(layout="other"), [make_candidate(1, None, file_sha256="f" * 64)])
    assert decision.verdict == "duplicate"
    assert "not_wb_screenshot" in codes(decision)


@pytest.mark.parametrize("fields", [desktop(layout="other"), desktop(is_wb_seller_chat=False)])
def test_row_3_not_a_wb_screenshot(fields: ExtractedFields) -> None:
    decision = run(fields)
    assert decision.verdict == "rejected"
    assert codes(decision)[0] == "not_wb_screenshot"


@pytest.mark.parametrize(
    ("quality", "code"),
    [(QualityFlags(blurred=True), "unreadable"), (QualityFlags(cropped_missing_panel=True), "cropped_missing_panel")],
)
def test_row_4_unreadable_when_key_fields_are_missing(quality: QualityFlags, code: str) -> None:
    decision = run(desktop(chat_id=None, text_norm=None, quality=quality))
    assert decision.verdict == "rejected"
    assert codes(decision)[0] == code


def test_row_4_blurred_but_readable_is_not_rejected() -> None:
    assert run(desktop(quality=QualityFlags(blurred=True))).verdict == "accepted"


def test_row_4_unreadable_layout() -> None:
    assert run(desktop(layout="unreadable")).verdict == "rejected"


def test_row_5_case_key() -> None:
    decision = run(desktop(), [make_candidate(1, desktop())])
    assert (decision.verdict, codes(decision)) == ("duplicate", ["case_key"])


def test_row_6_near_image_when_keys_are_unread() -> None:
    decision = run(None, [make_candidate(1, None, dhash=0)], dhash=0b111111)
    assert decision.verdict == "duplicate"
    assert codes(decision)[0] == "near_image"


def test_row_6_hash_alone_never_duplicates_readable_fields() -> None:
    decision = run(desktop(), [make_candidate(1, desktop(chat_id=CHAT_2, text_norm=OTHER_TEXT), dhash=0)], dhash=1)
    assert decision.verdict == "needs_review"
    assert codes(decision) == ["near_image_conflict"]


def test_row_6_soft_distance_is_only_recorded() -> None:
    decision = run(desktop(), [make_candidate(1, desktop(chat_id=CHAT_2, text_norm=OTHER_TEXT), dhash=0)], dhash=0xFFF)
    assert decision.verdict == "accepted"
    assert [match.kind for match in decision.matches] == ["near_image"]


def test_row_7_possible_duplicate() -> None:
    edited = TEXT[:-12] + "по телефону"
    decision = run(desktop(text_norm=edited), [make_candidate(1, desktop())])
    assert decision.verdict == "needs_review"
    assert "possible_duplicate" in codes(decision)


def test_row_8_extraction_failed() -> None:
    decision = decide(make_input(None, extraction=ExtractionResult(status="failed", error="timeout")), [], CFG)
    assert decision.verdict == "needs_review"
    assert decision.reasons[0].code == "extraction_failed"
    assert decision.reasons[0].detail == {"error": "timeout"}


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"disagreements": ("chat_id",)}, "extraction_disagreement_chat_id"),
        ({"agreement": {"time": 0.5}}, "extraction_disagreement_time"),
        ({"agreement": {"text": 0.79}}, "extraction_disagreement_text"),
    ],
)
def test_row_9_extraction_disagreement(changes: dict[str, Any], code: str) -> None:
    decision = run(desktop(**changes))
    assert decision.verdict == "needs_review"
    assert codes(decision) == [code]


def test_row_9_agreement_at_threshold_is_fine() -> None:
    assert run(desktop(agreement={"chat_id": 0.8, "time": 0.8, "text": 0.8})).verdict == "accepted"


def test_row_10_chat_id_unreadable() -> None:
    decision = run(desktop(chat_id=None))
    assert decision.verdict == "needs_review"
    assert codes(decision) == ["chat_id_unreadable"]


def test_row_10_extension_url_not_a_chat() -> None:
    decision = run(desktop(), channel="extension", hints=ClientHints(url="https://seller.wildberries.ru/feedbacks"))
    assert codes(decision) == ["url_not_chat"]


def test_row_11_review_time_unreadable() -> None:
    decision = run(desktop(review_at=None, review_time_precision=None))
    assert decision.verdict == "needs_review"
    assert codes(decision)[0] == "review_time_unreadable"


@pytest.mark.parametrize(
    ("review_at", "implausible"),
    [
        (NOW - timedelta(days=120), False),
        (NOW - timedelta(days=120, minutes=1), True),
        (NOW + timedelta(minutes=15), False),
        (NOW + timedelta(minutes=16), True),
    ],
)
def test_row_12_review_date_implausible(review_at: datetime, implausible: bool) -> None:
    decision = run(desktop(review_at=review_at))
    assert ("review_date_implausible" in codes(decision)) is implausible


def test_row_12_future_day_with_day_precision() -> None:
    tomorrow = NOW + timedelta(days=1)
    assert "review_date_implausible" in codes(run(desktop(review_at=tomorrow, review_time_precision="day")))
    assert "review_date_implausible" not in codes(run(desktop(review_at=NOW, review_time_precision="day")))


@pytest.mark.parametrize(("length", "weak"), [(24, True), (25, False)])
def test_row_13_weak_key_boundary_without_chat(length: int, weak: bool) -> None:
    decision = run(mobile((msg("я" * length, 9, 1),)))
    assert ("weak_key" in codes(decision)) is weak


def test_row_13_short_text_with_chat_is_not_weak() -> None:
    assert run(desktop(text_norm="ужасно")).verdict == "accepted"


@pytest.mark.parametrize("quality", [QualityFlags(photo_of_screen=True), QualityFlags(moire_or_glare=True)])
def test_row_14_photo_of_screen_is_rejected(quality: QualityFlags) -> None:
    decision = run(desktop(quality=quality))
    assert (decision.verdict, codes(decision)) == ("rejected", ["photo_of_screen"])


def test_row_15_possible_tampering() -> None:
    decision = run(desktop(quality=QualityFlags(looks_edited=True)))
    assert codes(decision) == ["possible_tampering"]
    assert decision.reasons[0].severity == "high"


@pytest.mark.parametrize(("minutes", "skew"), [(15, False), (16, True), (-16, True)])
def test_row_16_clock_skew(minutes: int, skew: bool) -> None:
    hints = ClientHints(
        url=f"https://seller.wildberries.ru/chat-with-clients?chatId={CHAT_1}",
        captured_at=NOW + timedelta(minutes=minutes),
    )
    decision = run(desktop(), channel="extension", hints=hints)
    assert ("clock_skew" in codes(decision)) is skew


def test_row_16_clock_skew_only_for_the_extension() -> None:
    hints = ClientHints(captured_at=NOW - timedelta(hours=5))
    assert run(desktop(), channel="web", hints=hints).verdict == "accepted"


@pytest.mark.parametrize(("hours", "late"), [(72, False), (73, True)])
def test_row_16_late_upload(hours: int, late: bool) -> None:
    hints = ClientHints(captured_at=NOW - timedelta(hours=hours))
    decision = run(desktop(), channel="extension", hints=hints, queued_at=NOW - timedelta(hours=hours))
    assert codes(decision) == (["late_upload"] if late else [])


@pytest.mark.parametrize(("uploads", "burst"), [(60, False), (61, True)])
def test_row_17_upload_burst(uploads: int, burst: bool) -> None:
    assert ("upload_burst" in codes(run(desktop(), uploads_last_hour=uploads))) is burst


def test_row_18_accepted_without_reasons() -> None:
    decision = run(desktop())
    assert decision == Decision(verdict="accepted")


@pytest.mark.parametrize(("percent", "audit"), [(100.0, True), (0.0, False)])
def test_row_18_audit_sample(percent: float, audit: bool) -> None:
    decision = decide(make_input(desktop()), [], replace(CFG, audit_percent=percent), rng=random.Random(1))
    assert decision.verdict == "accepted"
    assert decision.audit_requested is audit
    assert ("audit_sample" in codes(decision)) is audit


def test_audit_is_never_requested_for_other_verdicts() -> None:
    cfg = replace(CFG, audit_percent=100.0)
    assert not decide(make_input(desktop(chat_id=None)), [], cfg).audit_requested


def test_audit_share_follows_the_percent() -> None:
    cfg = replace(CFG, audit_percent=4.0)
    rng = random.Random(7)
    sampled = sum(decide(make_input(desktop()), [], cfg, rng=rng).audit_requested for _ in range(5000))
    assert 150 < sampled < 250


def test_reasons_accumulate_after_the_winning_one() -> None:
    decision = run(
        desktop(chat_id=None, quality=QualityFlags(looks_edited=True)),
        [make_candidate(1, desktop(chat_id=None))],
        uploads_last_hour=100,
    )
    assert decision.verdict == "duplicate"
    assert codes(decision) == ["case_key", "chat_id_unreadable", "possible_tampering", "upload_burst"]


# --- границы порогов сходства ---


@pytest.mark.parametrize(
    ("changed", "verdict", "code"),
    [
        (10, "duplicate", "case_key"),  # S_text = 0.90
        (11, "needs_review", "possible_duplicate"),  # 0.89
        (25, "needs_review", "possible_duplicate"),  # 0.75
        (26, "accepted", None),  # 0.74
    ],
)
def test_text_thresholds_on_the_same_chat_and_minute(changed: int, verdict: str, code: str | None) -> None:
    base = "а" * 100
    decision = run(desktop(text_norm="б" * changed + base[changed:]), [make_candidate(1, desktop(text_norm=base))])
    assert decision.verdict == verdict
    if code:
        assert code in codes(decision)


@pytest.mark.parametrize(("distance", "verdict"), [(6, "duplicate"), (7, "needs_review")])
def test_strict_dhash_boundary(distance: int, verdict: str) -> None:
    decision = run(None, [make_candidate(1, None, dhash=0)], dhash=(1 << distance) - 1)
    assert decision.verdict == verdict


# --- корень цепочки, порядок, детерминизм, устойчивость ---


def test_duplicate_points_to_the_earliest_non_duplicate_root() -> None:
    root = make_candidate(5, desktop(), received_at=NOW - timedelta(days=3))
    copy_of_root = make_candidate(6, desktop(), received_at=NOW - timedelta(days=4), verdict="duplicate")
    later = make_candidate(7, desktop(), received_at=NOW - timedelta(days=1))
    decision = run(desktop(), [later, copy_of_root, root])
    assert decision.duplicate_of == root.submission_id


def test_same_input_is_ignored_among_candidates() -> None:
    itself = make_candidate(0, desktop(), submission_id=INPUT_ID, file_sha256="f" * 64)
    assert run(desktop(), [itself]).verdict == "accepted"


MIXED = [
    make_candidate(1, desktop(), verdict="duplicate"),
    make_candidate(2, desktop(review_at=REVIEW_AT + timedelta(days=2))),
    make_candidate(3, desktop(chat_id=CHAT_2), user_id=USER_B),
    make_candidate(4, None, dhash=3),
    make_candidate(5, desktop(text_norm=DOM_EDITED, review_at=REVIEW_AT + timedelta(days=5))),
    make_candidate(6, mobile(), user_id=USER_B),
    make_candidate(7, desktop(), received_at=NOW - timedelta(days=9)),
]


def test_result_does_not_depend_on_candidate_order() -> None:
    expected = run(desktop(), MIXED, dhash=1, user_id=USER_B)
    assert expected.verdict == "duplicate"
    assert expected.duplicate_of == uuid.UUID(int=7)
    for order in itertools.islice(itertools.permutations(MIXED), 0, 5040, 97):
        assert run(desktop(), list(order), dhash=1, user_id=USER_B) == expected
    assert run(desktop(), list(reversed(MIXED)), dhash=1, user_id=USER_B) == expected


def test_decision_is_deterministic_including_the_default_audit_draw() -> None:
    cfg = replace(CFG, audit_percent=50.0)
    first = [decide(make_input(desktop(), submission_id=uuid.UUID(int=n)), MIXED, cfg) for n in range(100, 140)]
    second = [decide(make_input(desktop(), submission_id=uuid.UUID(int=n)), MIXED, cfg) for n in range(100, 140)]
    assert first == second
    assert {d.audit_requested for d in first if d.verdict == "accepted"} <= {True, False}


def test_decide_does_not_change_its_input() -> None:
    fields = desktop(agreement={"text": 0.95}, disagreements=("time",))
    inp = make_input(fields, dhash=1)
    candidates = list(MIXED)
    before = (copy.deepcopy(inp), copy.deepcopy(candidates))
    decide(inp, candidates, CFG)
    assert (inp, candidates) == before


EMPTY = ExtractedFields(layout="desktop_window", is_wb_seller_chat=True)
EMPTY_MOBILE = ExtractedFields(layout="mobile_app", is_wb_seller_chat=True, messages=(ChatMessage(text_norm=""),))


@pytest.mark.parametrize("fields", [None, EMPTY, EMPTY_MOBILE], ids=["no-fields", "empty-desktop", "empty-mobile"])
def test_empty_fields_everywhere_do_not_crash(fields: ExtractedFields | None) -> None:
    candidates = [
        make_candidate(1, None),
        make_candidate(2, EMPTY),
        make_candidate(3, EMPTY_MOBILE, dhash=0),
        make_candidate(4, desktop()),
        make_candidate(5, mobile()),
    ]
    decision = decide(make_input(fields, outcome=None, dhash=None), candidates, CFG)
    assert decision.verdict == "rejected"
    decision = decide(make_input(fields), candidates, CFG)
    assert decision.verdict in ("needs_review", "rejected")


def test_ok_status_without_fields_is_an_extraction_failure() -> None:
    decision = decide(make_input(None, extraction=ExtractionResult(status="ok")), [], CFG)
    assert codes(decision) == ["extraction_failed"]


def test_every_reason_carries_an_admin_detail_with_the_other_submission() -> None:
    decision = run(desktop(review_at=REVIEW_AT + timedelta(days=1)), [make_candidate(1, desktop())])
    detail = decision.reasons[0].detail
    assert detail["other_submission_id"] == str(uuid.UUID(int=1))
    assert "code" not in detail
