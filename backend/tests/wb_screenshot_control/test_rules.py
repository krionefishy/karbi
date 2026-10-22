import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import get_args
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ChatMessage,
    ClientHints,
    DecisionInput,
    ExtractedFields,
    ExtractionResult,
)
from backend.modules.wb_screenshot_control.domain.rules import (
    REASON_CODES,
    SEVERITY,
    Finding,
    behaviour_findings,
    chat_findings,
    compare_case,
    content_rejections,
    key_dated,
    key_text_len,
    keys_contradict,
    keys_unread,
)
from backend.modules.wb_screenshot_control.presentation.http import schemas
from backend.shared.settings.config import ScreenshotControlConfig

CFG = ScreenshotControlConfig()
MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
AT = datetime(2026, 10, 20, 3, 39, tzinfo=UTC)
CHAT = "0b8f3c2a-1d4e-4f6a-9b7c-2e5d8a1f3c40"
TEXT = "товар пришел с браком упаковка порвана продавец не отвечает на вопросы"

DESKTOP = ExtractedFields(
    layout="desktop_window",
    is_wb_seller_chat=True,
    chat_id=CHAT,
    chat_id_source="url",
    review_at=AT,
    review_time_precision="minute",
    text_norm=TEXT,
)


def candidate(fields: ExtractedFields | None, verdict: str = "accepted") -> Candidate:
    return Candidate(
        submission_id=uuid.UUID(int=1),
        user_id=uuid.UUID(int=2),
        received_at=NOW - timedelta(days=1),
        verdict=verdict,
        outcome="positive",
        file_sha256="0" * 64,
        fields=fields,
    )


def test_reason_codes_match_the_http_contract() -> None:
    assert get_args(schemas.ReasonCode) == REASON_CODES
    assert set(SEVERITY) == set(REASON_CODES)
    assert set(SEVERITY.values()) <= set(get_args(schemas.Severity))


def test_high_severity_marks_what_the_suspicion_queue_needs() -> None:
    high = {code for code, severity in SEVERITY.items() if severity == "high"}
    assert {"cross_user_duplicate", "same_chat_same_text_other_time", "same_text_time_other_chat"} <= high


def test_finding_turns_into_a_reason_with_its_severity() -> None:
    reason = Finding("cross_user_duplicate", None, {"x": 1}).reason()
    assert (reason.code, reason.severity, reason.detail) == ("cross_user_duplicate", "high", {"x": 1})


def test_compare_case_without_candidate_fields() -> None:
    assert compare_case(DESKTOP, candidate(None), CFG, MSK) is None


def test_compare_case_records_the_match_kind_and_scores() -> None:
    hit = compare_case(DESKTOP, candidate(DESKTOP), CFG, MSK)
    assert hit is not None
    assert (hit.code, hit.impact, hit.match.kind) == ("case_key", "duplicate", "case_key")
    assert hit.match.details == {"code": "case_key", "s_chat": 1.0, "s_time": 1.0, "s_text": 1.0}


def test_keys_unread() -> None:
    assert keys_unread(None)
    assert keys_unread(ExtractedFields(layout="desktop_window", is_wb_seller_chat=True))
    assert not keys_unread(replace(DESKTOP, chat_id=None))
    assert not keys_unread(
        ExtractedFields(layout="mobile_app", is_wb_seller_chat=True, messages=(ChatMessage("брак", 9, 1),))
    )


def test_keys_contradict() -> None:
    assert not keys_contradict(DESKTOP, DESKTOP, CFG, MSK)
    assert not keys_contradict(DESKTOP, None, CFG, MSK)
    assert keys_contradict(DESKTOP, replace(DESKTOP, chat_id="7d1e9a4b-5c2f-4e8d-a3b6-9f0c1e2d7a58"), CFG, MSK)
    assert keys_contradict(DESKTOP, replace(DESKTOP, text_norm="совсем другой текст про размер"), CFG, MSK)
    assert keys_contradict(DESKTOP, replace(DESKTOP, review_at=AT + timedelta(hours=1)), CFG, MSK)


def test_mobile_key_is_the_whole_client_history() -> None:
    history = (
        ChatMessage("брак", 9, 1, side="client"),
        ChatMessage("здравствуйте чем можем помочь", 9, 2, side="seller"),
        ChatMessage("шов разошелся", 9, 3, side="client"),
    )
    mobile = ExtractedFields(layout="mobile_app", is_wb_seller_chat=True, text_norm="шов разошелся", messages=history)
    assert key_text_len(mobile) == len("брак") + len("шов разошелся")
    assert key_text_len(DESKTOP) == len(TEXT)
    assert not key_dated(mobile)
    assert key_dated(replace(mobile, messages=(ChatMessage("брак", 9, 1, day=1, month=10),)))
    assert key_dated(DESKTOP)


def test_content_rejections() -> None:
    assert [f.code for f in content_rejections(replace(DESKTOP, layout="other"))] == ["not_wb_screenshot"]
    assert content_rejections(DESKTOP) == []


def make_input(**changes: object) -> DecisionInput:
    base = DecisionInput(
        submission_id=uuid.UUID(int=9),
        user_id=uuid.UUID(int=2),
        channel="extension",
        received_at=NOW,
        outcome="positive",
        file_sha256="f" * 64,
        dhash=None,
        extraction=ExtractionResult(status="ok", fields=DESKTOP),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_chat_url_prefix_comes_from_config() -> None:
    chat_url = ClientHints(url=f"https://seller.wildberries.ru/chat-with-clients?chatId={CHAT}")
    assert chat_findings(make_input(hints=chat_url), DESKTOP, CFG) == []
    assert [
        f.code for f in chat_findings(make_input(hints=ClientHints(url="https://evil.example/")), DESKTOP, CFG)
    ] == ["url_not_chat"]
    assert chat_findings(make_input(channel="web", hints=ClientHints(url="https://evil.example/")), DESKTOP, CFG) == []


def test_queued_upload_is_judged_by_lateness_not_clock_skew() -> None:
    queued = make_input(queued_at=NOW - timedelta(hours=1), hints=ClientHints(captured_at=NOW - timedelta(hours=1)))
    assert behaviour_findings(queued, CFG) == []
