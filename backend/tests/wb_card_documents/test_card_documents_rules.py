import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from backend.modules.notifications.application.templates import render
from backend.modules.wb_card_documents.application import (
    TEMPLATE_ALERT,
    TEMPLATE_BASELINE,
    TEMPLATE_COLLECTION_ERROR,
    TEMPLATE_DIGEST,
    TEMPLATE_STALE,
    alert_dedupe,
)
from backend.modules.wb_card_documents.domain import (
    STATE_EXPIRING,
    STATE_MISMATCH,
    STATE_MISSING,
    STATE_OK,
    STATE_REJECTED,
    CardState,
    PreviousState,
    evaluate,
    should_alert,
)
from backend.modules.wb_card_documents.infrastructure.wb import parse_card_documents

TODAY = date(2026, 10, 22)
NUMBER = "ЕАЭС N RU Д-CN.РА09.В.46306/26"


def document(**overrides) -> dict:
    item = {
        "id": 5512,
        "type": 2,
        "number": NUMBER,
        "startDate": "2026-10-14T00:00:00Z",
        "endDate": "2031-10-08T00:00:00Z",
        "isEndless": False,
        "verdict": {
            "verified": False,
            "status": 2,
            "reason": "document_not_found",
            "createdAt": "2026-10-14T18:48:24.555914+03:00",
        },
        "createdAt": "2026-10-14T18:40:00Z",
    }
    item.update(overrides)
    return item


def valid(**overrides) -> dict:
    verdict = {"verified": True, "status": 1, "reason": "valid", "createdAt": "2026-10-21T11:02:03.120001+03:00"}
    return document(verdict=verdict, **overrides)


def characteristics(number: str | None = NUMBER, start: str | None = "14.10.2026", end: str | None = "08.10.2031"):
    found = [{"id": 85, "name": "Цвет", "value": ["черный"]}]
    if number is not None:
        found.append({"id": 15001135, "name": "Номер декларации соответствия", "value": [number]})
    if start is not None:
        found.append({"id": 15001137, "name": "Дата регистрации сертификата/декларации", "value": [start]})
    if end is not None:
        found.append({"id": 15001138, "name": "Дата окончания действия сертификата/декларации", "value": [end]})
    return found


def raw_card(nm_id: int = 1504904633, *, items: list | None = None, overall: dict | None = None, **overrides) -> dict:
    card = {
        "nmID": nm_id,
        "vendorCode": "VK-1",
        "title": "Воздуходувка аккумуляторная",
        "subjectName": "Воздуходувки",
        "characteristics": characteristics(),
        "documents": {
            "items": [document()] if items is None else items,
            "overallVerdict": overall
            or {
                "isFullyChecked": True,
                "status": 2,
                "reason": "documents_invalid",
                "createdAt": "2026-10-14T18:48:25+03:00",
            },
            "excludeDocuments": False,
        },
    }
    card.update(overrides)
    return card


def state_of(raw: dict, today: date = TODAY) -> CardState:
    parsed = parse_card_documents(raw)
    assert parsed is not None
    return evaluate(parsed, today)


def test_the_parser_keeps_documents_verdicts_and_declaration() -> None:
    parsed = parse_card_documents(raw_card())

    assert parsed is not None
    assert (parsed.nm_id, parsed.vendor_code, parsed.has_block, parsed.exclude_documents) == (
        1504904633,
        "VK-1",
        True,
        False,
    )
    [item] = parsed.documents
    assert (item.number, item.start, item.end, item.endless) == (NUMBER, date(2026, 10, 14), date(2031, 10, 8), False)
    assert item.verdict is not None
    assert (item.verdict.verified, item.verdict.status, item.verdict.reason) == (False, 2, "document_not_found")
    assert item.verdict.created_at == datetime(2026, 10, 14, 15, 48, 24, 555914, tzinfo=UTC)
    assert parsed.overall is not None and parsed.overall.reason == "documents_invalid"
    assert (parsed.declared.number, parsed.declared.start, parsed.declared.end) == (
        NUMBER,
        date(2026, 10, 14),
        date(2031, 10, 8),
    )


def test_the_parser_tolerates_missing_fields() -> None:
    bare = parse_card_documents({"nmID": 7})
    assert bare is not None
    assert (bare.has_block, bare.documents, bare.overall, bare.declared.empty) == (False, (), None, True)

    partial = parse_card_documents(
        {"nmID": "8", "documents": {"items": [{"number": "X-1", "verdict": None}, "junk"]}, "characteristics": "?"}
    )
    assert partial is not None
    [item] = partial.documents
    assert (item.number, item.endless, item.end, item.verdict) == ("X-1", False, None, None)

    assert parse_card_documents({"vendorCode": "no id"}) is None
    assert parse_card_documents(None) is None


def test_a_number_and_dates_that_match_but_are_not_in_the_registry_are_a_rejection() -> None:
    """nmID 1504904633: характеристики и документ сходятся, реестр документа не знает."""
    state = state_of(raw_card())

    assert (state.state, state.reason, state.unknown_reason) == (STATE_REJECTED, "document_not_found", False)
    assert state.document is not None and state.document.number == NUMBER
    assert state.verdict_at == datetime(2026, 10, 14, 15, 48, 24, 555914, tzinfo=UTC)


def test_a_stale_overall_verdict_does_not_override_a_valid_document() -> None:
    """nmID 1175671003: документ valid от 21.10, общий вердикт documents_missing висит с 10.09."""
    card = raw_card(
        1175671003,
        items=[valid()],
        overall={
            "isFullyChecked": False,
            "status": 2,
            "reason": "documents_missing",
            "createdAt": "2026-09-10T10:00:00Z",
        },
    )

    state = state_of(card)

    assert (state.state, state.reason) == (STATE_OK, None)
    assert not state.problem


def test_an_unknown_reason_is_kept_as_it_came_and_counts_as_a_problem() -> None:
    weird = valid()
    weird["verdict"] = {"verified": False, "status": 3, "reason": "dates_out_of_range", "createdAt": None}

    state = state_of(raw_card(items=[weird]))

    assert (state.state, state.reason, state.unknown_reason) == (STATE_REJECTED, "dates_out_of_range", True)


def test_a_document_still_under_check_is_not_a_rejection() -> None:
    pending = valid()
    pending["verdict"] = {"verified": False, "status": 0}

    assert state_of(raw_card(items=[pending])).state == STATE_OK
    assert state_of(raw_card(items=[document(verdict=None)])).state == STATE_OK


@pytest.mark.parametrize(
    ("overall_reason", "declared", "exclude", "expected"),
    [
        ("documents_missing", None, False, STATE_MISSING),
        ("documents_invalid", NUMBER, False, STATE_MISSING),
        ("documents_invalid", None, False, STATE_OK),
        ("documents_missing", NUMBER, True, STATE_OK),
    ],
)
def test_empty_items_are_missing_only_when_a_document_is_due(
    overall_reason: str, declared: str | None, exclude: bool, expected: str
) -> None:
    card = raw_card(
        items=[],
        overall={"status": 2, "reason": overall_reason, "createdAt": "2026-10-01T00:00:00Z"},
        characteristics=characteristics(number=declared, start=None, end=None),
    )
    card["documents"]["excludeDocuments"] = exclude

    state = state_of(card)

    assert state.state == expected
    if expected == STATE_MISSING:
        assert state.reason == "documents_missing"


def test_a_card_without_the_documents_block_has_nothing_to_check() -> None:
    card = raw_card()
    del card["documents"]

    assert state_of(card).state == STATE_OK


@pytest.mark.parametrize(
    ("end", "threshold", "reason"),
    [
        ("2026-11-21", 30, None),
        ("2026-11-06", 30, None),
        ("2026-11-05", 14, None),
        ("2026-11-04", 14, None),
        ("2026-10-29", 7, None),
        ("2026-10-20", 0, "expired"),
    ],
)
def test_expiry_crosses_30_14_and_7_days(end: str, threshold: int, reason: str | None) -> None:
    card = raw_card(
        items=[valid(endDate=f"{end}T00:00:00Z")],
        characteristics=characteristics(end=".".join(reversed(end.split("-")))),
    )

    state = state_of(card)

    assert (state.state, state.threshold, state.reason) == (STATE_EXPIRING, threshold, reason)


def test_far_and_endless_documents_are_not_expiring() -> None:
    assert state_of(raw_card(items=[valid(endDate="2026-11-22T00:00:00Z")], characteristics=[])).state == STATE_OK
    endless = valid(endDate="2026-10-25T00:00:00Z", isEndless=True)
    assert state_of(raw_card(items=[endless], characteristics=[])).state == STATE_OK


def test_characteristics_that_disagree_with_the_document_are_a_mismatch() -> None:
    other_number = raw_card(items=[valid()], characteristics=characteristics(number="ЕАЭС N RU Д-CN.РА09.В.00000/26"))
    other_end = raw_card(items=[valid()], characteristics=characteristics(end="08.10.2030"))
    other_start = raw_card(items=[valid()], characteristics=characteristics(start="15.10.2026"))
    spacing = raw_card(items=[valid()], characteristics=characteristics(number=f"  {NUMBER.lower()} "))

    assert (state_of(other_number).state, state_of(other_number).reason) == (STATE_MISMATCH, "number_mismatch")
    assert state_of(other_end).reason == "end_date_mismatch"
    assert state_of(other_start).reason == "start_date_mismatch"
    assert state_of(spacing).state == STATE_OK


def test_the_declaration_is_also_found_by_name_for_other_subjects() -> None:
    card = raw_card(
        items=[valid()],
        characteristics=[
            {"id": 99, "name": "Номер сертификата соответствия", "value": "ЕАЭС RU С-1"},
            {"id": 98, "name": "Дата окончания действия сертификата", "value": ["01.01.2030"]},
        ],
    )
    parsed = parse_card_documents(card)

    assert parsed is not None
    assert (parsed.declared.number, parsed.declared.end) == ("ЕАЭС RU С-1", date(2030, 1, 1))
    assert state_of(card).reason == "number_mismatch"


def test_rejection_outranks_expiry_and_mismatch() -> None:
    card = raw_card(
        items=[valid(endDate="2026-10-25T00:00:00Z"), document(number="OTHER")],
        characteristics=characteristics(number="THIRD"),
    )

    assert state_of(card).state == STATE_REJECTED


def current(state: str, reason: str | None = None, threshold: int | None = None) -> CardState:
    return CardState(state, reason, None, None, threshold=threshold)


def test_alerts_follow_transitions_not_rereads() -> None:
    rejected = current(STATE_REJECTED, "document_not_found")

    assert should_alert(None, rejected)
    assert should_alert(PreviousState(STATE_OK, None, None, None), rejected)
    assert not should_alert(PreviousState(STATE_REJECTED, "document_not_found", None, None), rejected)
    assert should_alert(PreviousState(STATE_REJECTED, "documents_invalid", None, None), rejected)
    assert not should_alert(PreviousState(STATE_REJECTED, "document_not_found", None, None), current(STATE_OK))
    assert not should_alert(None, current(STATE_OK))


def test_expiry_alerts_once_per_threshold() -> None:
    assert should_alert(PreviousState(STATE_OK, None, None, None), current(STATE_EXPIRING, threshold=30))
    assert not should_alert(PreviousState(STATE_EXPIRING, None, None, 30), current(STATE_EXPIRING, threshold=30))
    assert should_alert(PreviousState(STATE_EXPIRING, None, None, 30), current(STATE_EXPIRING, threshold=14))
    assert should_alert(PreviousState(STATE_EXPIRING, None, None, 14), current(STATE_EXPIRING, threshold=7))
    assert not should_alert(PreviousState(STATE_EXPIRING, None, None, 7), current(STATE_EXPIRING, threshold=7))


def test_the_dedupe_key_tells_episodes_apart_and_stays_short() -> None:
    seller = uuid.uuid4()
    moment = datetime(2026, 10, 14, 15, 48, 24, tzinfo=UTC)
    since = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)

    def key(**overrides) -> str:
        values: dict[str, Any] = {
            "reason": "number_mismatch",
            "verdict_at": moment,
            "threshold": None,
            "since": since,
            "document_number": NUMBER,
            "document_end": date(2031, 10, 8),
        }
        values.update(overrides)
        return alert_dedupe(
            seller,
            15,
            STATE_MISMATCH,
            values["reason"],
            values["verdict_at"],
            values["threshold"],
            since=values["since"],
            document_number=values["document_number"],
            document_end=values["document_end"],
        )

    assert key().startswith(f"card-documents:alert:{seller}:15:mismatch:")
    assert key() == key()
    assert key(since=since + timedelta(hours=6)) != key()
    assert key(document_number="OTHER") != key()
    assert key(document_end=date(2032, 1, 1)) != key()
    assert key(threshold=14) != key(threshold=7)
    assert key(verdict_at=None) != key()
    long_reason = key(reason="x" * 2000)
    assert len(f"{uuid.uuid4()}:{long_reason}:{-1_001_234_567_890}") <= 255


def test_an_expired_document_is_its_own_threshold() -> None:
    week = PreviousState(STATE_EXPIRING, None, NUMBER, 7)
    expired = state_of(raw_card(items=[valid(endDate="2026-10-21T00:00:00Z")], characteristics=[]))

    assert (expired.state, expired.reason, expired.threshold) == (STATE_EXPIRING, "expired", 0)
    assert should_alert(week, expired)
    assert not should_alert(PreviousState(STATE_EXPIRING, "expired", NUMBER, 0), expired)
    assert state_of(raw_card(items=[valid(endDate="2026-10-22T00:00:00Z")], characteristics=[])).threshold == 7


def test_status_two_with_a_valid_reason_is_rejected_without_calling_it_valid() -> None:
    odd = valid()
    odd["verdict"] = {"verified": False, "status": 2, "reason": "valid", "createdAt": None}
    silent = valid()
    silent["verdict"] = {"verified": False, "status": 2}

    assert (state_of(raw_card(items=[odd])).state, state_of(raw_card(items=[odd])).reason) == (
        STATE_REJECTED,
        "status_2",
    )
    assert state_of(raw_card(items=[silent])).reason == "status_2"
    text = render(TEMPLATE_ALERT, {"state": "rejected", "reason": "status_2", "nm_id": 1})
    assert "причину не указал" in text and "действителен" not in text


def test_the_digest_fits_telegram_on_a_mass_rejection() -> None:
    items = [
        {
            "nm_id": 1_500_000_000 + index,
            "vendor_code": f"Артикул продавца очень длинный {index}",
            "title": "Воздуходувка аккумуляторная садовая с длинным названием для проверки " * 2,
            "state": "rejected",
            "reason": "document_not_found",
            "document_number": NUMBER,
            "document_end": "2031-10-08",
            "days_left": 1812,
        }
        for index in range(40)
    ]

    text = render(TEMPLATE_DIGEST, {"seller_name": "ИП Мунаева", "items": items})

    assert len(text) <= 4096
    assert text.startswith("ИП Мунаева: 40 карточек с проблемами в документах")
    shown = text.count("• ")
    assert 0 < shown < 40
    assert f"…и ещё {40 - shown} " in text


def test_times_in_messages_are_moscow_and_the_digest_hour_is_configurable() -> None:
    stale = render(TEMPLATE_STALE, {"seller_name": "ИП", "hours": 9, "collected_at": "2026-10-21T22:30:00+00:00"})
    assert "22 окт. 01:30 МСК" in stale and "UTC" not in stale

    digest = render(
        TEMPLATE_DIGEST,
        {"seller_name": "ИП", "stale": True, "collected_at": "2026-10-21T22:30:00+00:00", "items": []},
    )
    assert "последний сбор 22 окт. 01:30 МСК" in digest

    baseline = render(TEMPLATE_BASELINE, {"seller_name": "ИП", "cards": 5, "problems": 1, "digest_time": "9:30"})
    assert "в дайджесте в 9:30" in baseline


def test_templates_render_every_message() -> None:
    card = {
        "seller_name": "ИП Мунаева",
        "nm_id": 1504904633,
        "vendor_code": "VK-1",
        "title": "Воздуходувка",
        "state": "expiring",
        "reason": None,
        "document_number": NUMBER,
        "document_end": "2026-11-04",
        "days_left": 13,
    }
    alert = render(TEMPLATE_ALERT, card)
    assert "истекает срок документа" in alert
    assert "осталось 13 дней" in alert
    assert NUMBER in alert

    unknown = render(TEMPLATE_ALERT, {**card, "state": "rejected", "reason": "dates_out_of_range", "days_left": None})
    assert "Причина WB: dates_out_of_range" in unknown

    assert "взяли под наблюдение 120 карточек" in render(
        TEMPLATE_BASELINE, {"seller_name": "ИП", "cards": 120, "problems": 3}
    )
    assert "3 карточки с проблемами" in render(TEMPLATE_BASELINE, {"seller_name": "ИП", "cards": 120, "problems": 3})
    digest = render(TEMPLATE_DIGEST, {"seller_name": "ИП", "items": [card, {**card, "state": "missing"}]})
    assert digest.startswith("ИП: 2 карточки с проблемами в документах")
    assert "не работает больше 9 ч." in render(TEMPLATE_STALE, {"seller_name": "ИП", "hours": 9})
    assert "«Контент»" in render(TEMPLATE_COLLECTION_ERROR, {"seller_name": "ИП", "error": "нет доступа"})
