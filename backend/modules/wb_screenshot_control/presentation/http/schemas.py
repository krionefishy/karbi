"""Контракт HTTP «Контроля скриншотов ДБС» — единственный источник для фронта и расширения.

Типы фронта (`frontend/src/features/screenshotControl/types.ts`) повторяют этот файл вручную;
полная схема — `uv run python -m backend.commands.export_openapi --tag wb-screenshot-control`.
Меняется контракт только вместе с этим файлом.

Ошибки — как во всём проекте: `{"detail": "<текст по-русски>"}`. Машинный код ошибки —
заголовок `X-Error-Code` со значением из `ErrorCode`; это новая для проекта конвенция, её
вводит этот модуль. Время — ISO-8601 в UTC, отчётные периоды — даты по Москве.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.modules.wb_screenshot_control.domain import PAIRING_CODE_PATTERN

Outcome = Literal["positive", "amended", "deleted"]
"""Подпись менеджера: «полож», «доп», «удален»."""
OutcomeSource = Literal["button", "caption", "form", "admin"]
Verdict = Literal["accepted", "duplicate", "needs_review", "rejected"]
Status = Literal["awaiting_outcome", "processing", "accepted", "duplicate", "needs_review", "rejected"]
"""Что видит менеджер: вердикт либо одно из двух состояний до него."""
Channel = Literal["extension", "telegram", "web"]
Stage = Literal["processing", "extracted", "decided"]
Mode = Literal["off", "shadow", "enforce"]
Severity = Literal["info", "warn", "high"]
Suspicion = Literal["high", "medium", "low"]
ImageKind = Literal["thumb", "original"]
Layout = Literal["desktop_window", "desktop_viewport", "mobile_app", "other", "unreadable"]
CaseKind = Literal["desktop", "mobile"]
ChatIdSource = Literal["url", "image"]
TimePrecision = Literal["minute", "day"]
ExtractionStatus = Literal["ok", "failed"]
StorageState = Literal["staged", "uploading", "stored", "delete_requested", "deleted", "failed"]

ReasonCode = Literal[
    # совпадения
    "exact_file",
    "case_key",
    "near_image",
    "near_image_conflict",
    "cross_user_duplicate",
    "caption_conflict",
    "same_chat_same_text_other_time",
    "same_chat_similar_text",
    "same_text_time_other_chat",
    "same_text_other_time",
    "messages_overlap",
    "possible_duplicate",
    "order_conflict",
    "weak_key",
    # распознавание
    "extraction_disagreement_chat_id",
    "extraction_disagreement_time",
    "extraction_disagreement_text",
    "extraction_failed",
    "chat_id_unreadable",
    "review_time_unreadable",
    "review_date_implausible",
    "url_not_chat",
    "not_wb_screenshot",
    "unreadable",
    "cropped_missing_panel",
    # подлинность и поведение
    "photo_of_screen",
    "possible_tampering",
    "late_upload",
    "clock_skew",
    "upload_burst",
    # прочее
    "no_outcome",
    "audit_sample",
]
"""Полный словарь причин для админа. Менеджер видит только `ManagerReasonCode`."""

ManagerReasonCode = Literal[
    "already_uploaded",
    "under_review",
    "unreadable",
    "cropped_missing_panel",
    "not_wb_screenshot",
    "no_outcome",
]
"""Узкий словарь для менеджера: пороги и детали эвристик не раскрываются."""

MatchKind = Literal["exact_file", "near_image", "case_key", "chat_text", "text_time", "messages"]
ResolveDecision = Literal["approve", "decline", "duplicate_of", "not_duplicate", "audit_ok"]
QueueKind = Literal["review", "duplicates", "audit", "all"]
ActorKind = Literal["system", "user", "admin"]
ManagerFlag = Literal["high_duplicate_rate", "high_review_rate", "high_reject_rate", "upload_burst"]

ErrorCode = Literal[
    "not_found",
    "file_too_large",
    "unsupported_type",
    "too_small",
    "bad_image",
    "bad_outcome",
    "outcome_required",
    "idempotency_conflict",
    "outcome_locked",
    "period_too_long",
    "storage_full",
    "rate_limited",
    "limiter_unavailable",
    "module_off",
    "code_invalid",
    "bad_token",
    "install_revoked",
    "user_blocked",
    "upgrade_required",
    "exact_key_conflict",
    "note_required",
    "duplicate_target_invalid",
    "telegram_disabled",
]


class ErrorResponse(BaseModel):
    """Тело любой ошибки модуля; машинный код — в заголовке `X-Error-Code`."""

    detail: str


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- общие ---


class Period(BaseModel):
    """Отчётный период: даты по Москве, обе включительно."""

    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


class SubmissionFields(BaseModel):
    """Что прочитано со скриншота — менеджеру, чтобы проверить глазами."""

    review_at: datetime | None
    stars: int | None = Field(ge=1, le=5)
    text_preview: str | None = Field(max_length=200)
    chat_tail: str | None = Field(description="Последние 6 символов chatId", max_length=6)


class SubmissionOut(BaseModel):
    """Скриншот менеджера; только свой."""

    id: uuid.UUID
    channel: Channel
    received_at: datetime
    outcome: Outcome | None
    status: Status
    reason_code: ManagerReasonCode | None
    reason_text: str | None = Field(description="Готовая русская фраза для показа")
    duplicate_received_at: datetime | None = Field(
        description="Когда загружен оригинал; чей он — не раскрывается",
    )
    fields: SubmissionFields | None
    thumb_url: str | None = Field(description="Presigned GET миниатюры, живёт image_url_ttl_seconds")


class SubmissionPage(BaseModel):
    items: list[SubmissionOut]
    next_cursor: str | None


class OutcomeCounts(BaseModel):
    positive: int
    amended: int
    deleted: int
    accepted_total: int


class Summary(BaseModel):
    """Плашка KPI менеджера. KPI — только `accepted`; `needs_review` не засчитан, пока его не одобрят."""

    period: Period
    mode: Mode
    preliminary: bool = Field(description="Режим shadow или период раньше kpi_from: цифры пробные")
    counts: OutcomeCounts
    pending_review: int
    awaiting_outcome: int
    processing: int
    duplicates: int
    rejected: int


class OutcomeUpdate(_Request):
    outcome: Outcome


class ImageUrlOut(BaseModel):
    """Presigned GET напрямую из S3. Оригинал отдаётся как attachment, миниатюра — inline."""

    url: str | None
    expires_at: datetime | None
    available: bool = Field(description="false — файл очищен по сроку или ещё не выгружен")


class OutcomeOption(BaseModel):
    value: Outcome
    caption: str = Field(description="Как пишут менеджеры: полож, доп, удален")
    label: str


class ManagerConfigOut(BaseModel):
    mode: Mode
    telegram_enabled: bool
    outcomes: list[OutcomeOption]
    max_upload_bytes: int
    accepted_types: list[str]
    min_side_px: int
    extension_url: str
    extension_min_version: str
    bot_username: str | None


# --- подключения менеджера ---


class PairingCodeOut(BaseModel):
    code: str = Field(pattern=PAIRING_CODE_PATTERN, description="8 символов Crockford base32")
    expires_at: datetime


class ExtensionInstallOut(BaseModel):
    id: uuid.UUID
    browser: str
    ext_version: str | None
    created_at: datetime
    last_seen_at: datetime | None
    last_used_at: datetime | None


class TelegramLinkOut(BaseModel):
    url: str = Field(description="t.me/<бот>?start=<токен>, одноразовая")
    expires_at: datetime


class TelegramStatusOut(BaseModel):
    enabled: bool
    linked: bool
    username: str | None
    linked_at: datetime | None


# --- расширение ---


class ExtensionPairRequest(_Request):
    code: str = Field(pattern=PAIRING_CODE_PATTERN, description="Код со страницы, верхний регистр")
    install_id: str = Field(min_length=8, max_length=64)
    browser: str = Field(default="", max_length=255)
    ext_version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")


class ExtensionPairOut(BaseModel):
    token: str = Field(description="mas_…, показывается один раз")
    username: str
    min_version: str
    allowed_url_prefixes: list[str]


class ExtensionMeOut(BaseModel):
    username: str
    min_version: str
    mode: Mode
    allowed_url_prefixes: list[str]
    summary: Summary
    server_time: datetime


class ExtensionSubmissionList(BaseModel):
    items: list[SubmissionOut]


# --- админ ---


class UserRef(BaseModel):
    id: uuid.UUID
    username: str = Field(description="Пустая строка, если сотрудник удалён")


class ReasonOut(BaseModel):
    code: ReasonCode
    severity: Severity
    detail: str | None
    other_submission_id: uuid.UUID | None


class MatchOut(BaseModel):
    """С чем совпал скриншот: админ видит пару бок-о-бок."""

    other_id: uuid.UUID
    other_user: UserRef
    other_received_at: datetime
    other_outcome: Outcome | None
    other_verdict: Verdict | None
    kind: MatchKind
    score: float = Field(ge=0, le=1)
    thumb_url: str | None


class FieldConfidence(BaseModel):
    chat_id: float | None = Field(ge=0, le=1)
    time: float | None = Field(ge=0, le=1)
    text: float | None = Field(ge=0, le=1)


class QualityFlags(BaseModel):
    blurred: bool
    cropped_missing_panel: bool
    photo_of_screen: bool
    moire_or_glare: bool
    looks_edited: bool
    dark_overlay_or_popup: bool


class ClientMessage(BaseModel):
    text: str
    at: datetime | None
    time_precision: TimePrecision | None


class AdminFields(BaseModel):
    """Распознанные поля полностью. Имя покупателя не хранится и не отдаётся: только признак, что оно было."""

    layout: Layout | None
    case_kind: CaseKind | None
    chat_id: str | None
    chat_id_source: ChatIdSource | None
    key_time: datetime | None
    key_time_precision: TimePrecision | None
    review_at: datetime | None
    review_text: str | None
    review_text_truncated: bool
    stars: int | None = Field(ge=1, le=5)
    article: str | None
    client_messages: list[ClientMessage]
    buyer_name_seen: bool
    quality: QualityFlags | None
    confidence: FieldConfidence


class AdminSubmission(BaseModel):
    id: uuid.UUID
    user: UserRef
    channel: Channel
    received_at: datetime
    outcome: Outcome | None
    outcome_source: OutcomeSource | None
    caption_raw: str | None
    stage: Stage
    verdict: Verdict | None
    auto_verdict: Verdict | None
    reasons: list[ReasonOut]
    suspicion: Suspicion | None = Field(
        description=(
            "high — cross_user_duplicate, same_chat_same_text_other_time, same_text_time_other_chat, "
            "possible_tampering; medium — прочие needs_review и дубли своего; low — аудит"
        )
    )
    duplicate_of: uuid.UUID | None
    audit_requested: bool
    matches: list[MatchOut]
    fields: AdminFields | None
    agreement: FieldConfidence | None = Field(description="Согласие VLM и OCR по полю, 0..1")
    thumb_url: str | None


class ClientMeta(BaseModel):
    ext_version: str | None
    viewport_w: int | None
    viewport_h: int | None
    dpr: float | None
    user_agent: str | None
    telegram_compressed: bool


class FileMeta(BaseModel):
    has_exif: bool
    exif_software: str | None
    has_alpha: bool


class StorageObjectOut(BaseModel):
    kind: Literal["orig", "thumb"]
    state: StorageState
    stored_at: datetime | None
    deleted_at: datetime | None


class FileOut(BaseModel):
    id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime: str
    size_bytes: int
    width: int
    height: int
    meta: FileMeta
    objects: list[StorageObjectOut]
    submissions_count: int = Field(description="Сколько раз этот же файл присылали, всеми каналами")


class ExtractionOut(BaseModel):
    status: ExtractionStatus
    pipeline_version: str
    vlm_model: str | None
    prompt_version: str | None
    attempts: int
    error: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    created_at: datetime


class ActorOut(BaseModel):
    kind: ActorKind
    user: UserRef | None


class VerdictEventOut(BaseModel):
    at: datetime
    actor: ActorOut
    from_verdict: Verdict | None
    to_verdict: Verdict | None
    reasons: list[ReasonOut]
    note: str | None


class AdminSubmissionDetail(AdminSubmission):
    client_url: str | None
    client_captured_at: datetime | None
    client_tz_offset_min: int | None
    client_meta: ClientMeta
    install_id: uuid.UUID | None
    file: FileOut
    extraction: ExtractionOut | None
    events: list[VerdictEventOut]
    decided_at: datetime | None
    reviewed_by: UserRef | None
    reviewed_at: datetime | None
    review_note: str | None


class QueueCounts(BaseModel):
    review: int
    duplicates: int
    audit: int


class AdminQueuePage(BaseModel):
    items: list[AdminSubmission]
    next_cursor: str | None
    counts: QueueCounts


class ResolveRequest(_Request):
    """Решение админа. Комментарий обязателен для decline и для снятия уже засчитанного."""

    decision: ResolveDecision
    duplicate_of: uuid.UUID | None = None
    outcome: Outcome | None = None
    note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def target_only_for_duplicate(self) -> "ResolveRequest":
        if (self.decision == "duplicate_of") != (self.duplicate_of is not None):
            raise ValueError("duplicate_of задаётся тогда и только тогда, когда decision = duplicate_of")
        return self


class ReprocessOut(BaseModel):
    id: uuid.UUID
    stage: Stage


class FileEraseOut(BaseModel):
    id: uuid.UUID
    objects: list[StorageObjectOut]


class ManagerStats(BaseModel):
    user: UserRef
    total: int
    accepted: int
    positive: int
    amended: int
    deleted: int
    duplicate: int
    needs_review: int
    rejected: int
    processing: int
    duplicate_rate: float = Field(ge=0, le=1)
    review_rate: float = Field(ge=0, le=1)
    reject_rate: float = Field(ge=0, le=1)
    last_submission_at: datetime | None
    flags: list[ManagerFlag]


class ManagersOut(BaseModel):
    period: Period
    items: list[ManagerStats]


class PipelineStages(BaseModel):
    processing: int
    extracted: int


class StorageStats(BaseModel):
    objects: dict[StorageState, int]
    stored_bytes: int
    oldest_staged_age_s: int | None
    staging_used_percent: float | None


class ChannelCounts(BaseModel):
    extension: int
    telegram: int
    web: int


class PipelineOut(BaseModel):
    """Экран «Конвейер»: очередь, задержка «приём → вердикт», распознавание, хранилище."""

    mode: Mode
    telegram_enabled: bool
    stages: PipelineStages
    oldest_processing_age_s: int | None
    p50_s: float | None
    p95_s: float | None
    vlm_error_rate_1h: float | None = Field(ge=0, le=1)
    extraction_failed_today: int
    tokens_today: int
    storage: StorageStats
    channels_today: ChannelCounts


class ThresholdsOut(BaseModel):
    text_dup: float
    text_maybe: float
    min_text_len: int
    vlm_ocr_text: float
    near_dhash_strict: int
    near_tile_strict: float
    near_dhash_soft: int
    near_tile_soft: float


class AdminConfigOut(BaseModel):
    """Действующая конфигурация без секретов."""

    mode: Mode
    kpi_from: date | None
    telegram_enabled: bool
    pipeline_version: str
    vision_model: str | None
    s3_bucket: str
    retention_original_days: int
    retention_thumb_days: int
    retention_fields_days: int
    dedupe_window_days: int
    outcome_ttl_hours: int
    review_age_max_days: int
    max_upload_bytes: int
    min_side_px: int
    uploads_per_minute: int
    uploads_per_hour: int
    burst_per_hour: int
    audit_percent: float
    thresholds: ThresholdsOut
    extension_min_version: str
    allowed_url_prefixes: list[str]


class AdminTelegramLinkOut(BaseModel):
    id: uuid.UUID
    user: UserRef
    telegram_username: str
    linked_at: datetime
