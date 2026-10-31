"""Типы стыков ↔ JSON колонок: `extractions.fields`, `submissions.reasons` и `matches`."""

import uuid
from dataclasses import asdict, fields
from datetime import date, datetime
from typing import Any

from backend.modules.wb_screenshot_control.domain.normalize import ScreenDateTime
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    ChatMessage,
    ExtractedFields,
    Match,
    QualityFlags,
    Reason,
)

PERSONAL_KEYS = frozenset({"buyer_name"})


def without_personal(value: Any) -> Any:
    """Имя покупателя в открытом виде в базу не пишется, на какой бы глубине ответа модели оно ни было."""
    if isinstance(value, dict):
        return {key: without_personal(item) for key, item in value.items() if key not in PERSONAL_KEYS}
    if isinstance(value, list):
        return [without_personal(item) for item in value]
    return value


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def fields_to_json(value: ExtractedFields) -> dict[str, Any]:
    return json_safe(asdict(value))


def fields_from_json(data: dict[str, Any]) -> ExtractedFields:
    known = {item.name for item in fields(ExtractedFields)}
    values = {key: item for key, item in data.items() if key in known}
    review_at = values.get("review_at")
    values["review_at"] = datetime.fromisoformat(review_at) if review_at else None
    values["messages"] = tuple(ChatMessage(**message) for message in values.get("messages") or ())
    values["quality"] = QualityFlags(**(values.get("quality") or {}))
    values["agreement"] = dict(values.get("agreement") or {})
    values["disagreements"] = tuple(values.get("disagreements") or ())
    screen = values.get("review_screen")
    values["review_screen"] = ScreenDateTime(**screen) if isinstance(screen, dict) else None
    return ExtractedFields(**values)


def reason_to_json(reason: Reason) -> dict[str, Any]:
    return {"code": reason.code, "severity": reason.severity, "detail": json_safe(reason.detail)}


def match_to_json(match: Match) -> dict[str, Any]:
    return {
        "other_submission_id": str(match.other_submission_id),
        "kind": match.kind,
        "score": match.score,
        "details": json_safe(match.details),
    }
