"""Что видит менеджер: узкий код и готовая фраза вместо внутренних причин.

Пороги, совпавшие поля и чужие имена не раскрываются — иначе под них начнут подбирать скрины.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, get_args

ManagerReasonCode = Literal[
    "already_uploaded",
    "under_review",
    "unreadable",
    "cropped_missing_panel",
    "photo_of_screen",
    "not_wb_screenshot",
    "no_outcome",
]
"""Тот же закрытый словарь, что `ManagerReasonCode` HTTP-контракта; расхождение ловит тест."""

MANAGER_REASON_CODES: tuple[str, ...] = get_args(ManagerReasonCode)

ACCEPTED_TEXT = "Принят"

MANAGER_TEXTS: dict[ManagerReasonCode, str] = {
    "already_uploaded": "Этот скриншот уже загружен ранее",
    "under_review": "Принят, проверяется",
    "unreadable": "Нужен чёткий скриншот всего окна",
    "cropped_missing_panel": "Нужен скриншот всего окна без обрезки",
    "photo_of_screen": "Нужен скриншот, а не фото экрана",
    "not_wb_screenshot": "Это не скриншот чата WB",
    "no_outcome": "Не выбран исход",
}

# Если у отклонения несколько причин, менеджеру называется первая по этому списку: сначала то,
# без чего скрин не засчитать вовсе.
_REJECT_PRIORITY: tuple[ManagerReasonCode, ...] = (
    "no_outcome",
    "not_wb_screenshot",
    "photo_of_screen",
    "cropped_missing_panel",
    "unreadable",
)


@dataclass(frozen=True, slots=True)
class ManagerReason:
    code: ManagerReasonCode | None
    text: str


def manager_reason(verdict: str | None, reason_codes: Iterable[str] = ()) -> ManagerReason | None:
    """Код и фраза для менеджера по вердикту и внутренним причинам. Нет вердикта — нечего показать."""
    if verdict is None:
        return None
    if verdict == "accepted":
        return ManagerReason(None, ACCEPTED_TEXT)
    if verdict == "duplicate":
        return _reason("already_uploaded")
    if verdict == "needs_review":
        return _reason("under_review")
    codes = set(reason_codes)
    return _reason(next((code for code in _REJECT_PRIORITY if code in codes), "unreadable"))


def _reason(code: ManagerReasonCode) -> ManagerReason:
    return ManagerReason(code, MANAGER_TEXTS[code])
