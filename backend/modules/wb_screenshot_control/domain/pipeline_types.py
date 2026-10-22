"""Типы на стыках конвейера: распознавание отдаёт `ExtractionResult`, решение принимает `DecisionInput`
и кандидатов, возвращает `Decision`. Все три части чистые и не знают друг о друге, только об этих типах.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Severity = Literal["info", "warn", "high"]
ChatSide = Literal["client", "seller"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """Видимое сообщение переписки: время как на экране и нормализованный текст."""

    text_norm: str
    hour: int | None = None
    minute: int | None = None
    day: int | None = None
    month: int | None = None
    side: ChatSide | None = None


@dataclass(frozen=True, slots=True)
class QualityFlags:
    blurred: bool = False
    cropped_missing_panel: bool = False
    photo_of_screen: bool = False
    moire_or_glare: bool = False
    looks_edited: bool = False
    dark_overlay_or_popup: bool = False


@dataclass(frozen=True, slots=True)
class ClientHints:
    """Что клиент прислал рядом с картинкой. Время клиента доверенным не считается."""

    url: str | None = None
    captured_at: datetime | None = None
    tz_offset_min: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractedFields:
    """Нормализованные поля. Имя покупателя здесь только как HMAC, в открытом виде не живёт нигде."""

    layout: str
    is_wb_seller_chat: bool
    chat_id: str | None = None
    chat_id_source: Literal["url", "image"] | None = None
    review_at: datetime | None = None
    review_time_precision: Literal["minute", "day"] | None = None
    stars: int | None = None
    review_text: str | None = None
    text_norm: str | None = None
    text_truncated: bool = False
    article_norm: str | None = None
    buyer_hmac: str | None = None
    messages: tuple[ChatMessage, ...] = ()
    quality: QualityFlags = QualityFlags()
    agreement: dict[str, float] = field(default_factory=dict)
    disagreements: tuple[Literal["chat_id", "time", "text"], ...] = ()

    @property
    def case_kind(self) -> Literal["desktop", "mobile"]:
        return "mobile" if self.layout == "mobile_app" else "desktop"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    status: Literal["ok", "failed"]
    fields: ExtractedFields | None = None
    vlm_model: str | None = None
    vlm_prompt: str | None = None
    vlm_json: dict[str, Any] | None = None
    ocr_json: dict[str, Any] | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Candidate:
    """Ранее принятый скрин, с которым сравниваем. Отклонённые нечитаемые кандидатами не бывают."""

    submission_id: uuid.UUID
    user_id: uuid.UUID
    received_at: datetime
    verdict: str | None
    outcome: str | None
    file_sha256: str
    dhash: int | None = None
    fields: ExtractedFields | None = None


@dataclass(frozen=True, slots=True)
class DecisionInput:
    submission_id: uuid.UUID
    user_id: uuid.UUID
    channel: Literal["extension", "telegram", "web"]
    received_at: datetime
    outcome: str | None
    file_sha256: str
    dhash: int | None
    extraction: ExtractionResult
    hints: ClientHints = ClientHints()
    uploads_last_hour: int = 0
    queued_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    severity: Severity = "info"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Match:
    other_submission_id: uuid.UUID
    kind: Literal["exact_file", "near_image", "case_key", "chat_text", "text_time", "messages"]
    score: float
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    verdict: Literal["accepted", "duplicate", "needs_review", "rejected"]
    reasons: tuple[Reason, ...] = ()
    duplicate_of: uuid.UUID | None = None
    matches: tuple[Match, ...] = ()
    audit_requested: bool = False
