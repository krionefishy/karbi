from typing import get_args

import pytest

from backend.modules.wb_screenshot_control.domain.manager_reasons import (
    MANAGER_REASON_CODES,
    MANAGER_TEXTS,
    ManagerReason,
    manager_reason,
)
from backend.modules.wb_screenshot_control.domain.rules import REASON_CODES
from backend.modules.wb_screenshot_control.presentation.http import schemas


def test_manager_codes_match_the_http_contract() -> None:
    assert get_args(schemas.ManagerReasonCode) == MANAGER_REASON_CODES
    assert set(MANAGER_TEXTS) == set(MANAGER_REASON_CODES)


def test_no_verdict_shows_nothing() -> None:
    assert manager_reason(None) is None


def test_accepted() -> None:
    assert manager_reason("accepted") == ManagerReason(None, "Принят")


@pytest.mark.parametrize(
    "reasons",
    [["case_key"], ["exact_file", "cross_user_duplicate", "caption_conflict"], ["near_image"]],
)
def test_duplicate_reveals_neither_details_nor_owner(reasons: list[str]) -> None:
    assert manager_reason("duplicate", reasons) == ManagerReason("already_uploaded", "Этот скриншот уже загружен ранее")


@pytest.mark.parametrize("code", [c for c in REASON_CODES if c not in ("no_outcome", "not_wb_screenshot")])
def test_needs_review_is_always_the_same_phrase(code: str) -> None:
    assert manager_reason("needs_review", [code]) == ManagerReason("under_review", "Принят, проверяется")


@pytest.mark.parametrize(
    ("reasons", "code", "text"),
    [
        (["unreadable"], "unreadable", "Нужен чёткий скриншот всего окна"),
        (["cropped_missing_panel"], "cropped_missing_panel", "Нужен скриншот всего окна без обрезки"),
        (["not_wb_screenshot"], "not_wb_screenshot", "Это не скриншот чата WB"),
        (["no_outcome"], "no_outcome", "Не выбран исход"),
        (["unreadable", "no_outcome"], "no_outcome", "Не выбран исход"),
        (["cropped_missing_panel", "not_wb_screenshot"], "not_wb_screenshot", "Это не скриншот чата WB"),
        ([], "unreadable", "Нужен чёткий скриншот всего окна"),
    ],
)
def test_rejected_says_what_to_fix(reasons: list[str], code: str, text: str) -> None:
    assert manager_reason("rejected", reasons) == ManagerReason(code, text)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [*MANAGER_TEXTS.values(), "Принят"])
def test_phrases_are_plain_russian(text: str) -> None:
    lowered = text.lower()
    assert "успешно" not in lowered
    assert "пожалуйста" not in lowered
    assert "!" not in text
    assert any("а" <= char <= "я" for char in lowered)
