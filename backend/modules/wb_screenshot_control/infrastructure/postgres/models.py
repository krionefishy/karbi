"""Схема `wb_screenshot_control`.

Сотрудники — `platform.users.id` без внешнего ключа: схемы модулей между собой не связываются.
Outbox и inbox — общие (`wb_core.outbox_events`, `wb_core.inbox_events`), своих здесь нет.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BIT, CHAR, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.modules.wb_screenshot_control.domain import (
    CASE_KINDS,
    CHANNELS,
    CHAT_ID_SOURCES,
    EXTRACTION_STATUSES,
    IMAGE_TYPES,
    OUTCOME_SOURCES,
    OUTCOMES,
    STAGES,
    STORAGE_KINDS,
    STORAGE_STATES,
    TIME_PRECISIONS,
    VERDICTS,
)


class WBScreenshotControlBase(DeclarativeBase):
    metadata = MetaData(schema="wb_screenshot_control")


def one_of(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({listed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


class FileModel(WBScreenshotControlBase):
    """Файл по содержимому: один на sha256, сколько бы раз и кем его ни присылали.

    Где лежат байты, знает `storage_objects`; хеши для похожих картинок считает воркер.
    """

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(String(16), nullable=False)
    width: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    height: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dhash: Mapped[str | None] = mapped_column(BIT(64), nullable=True)
    tile_hashes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pinned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pinned_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_sc_files_size"),
        CheckConstraint(one_of("mime", IMAGE_TYPES), name="ck_sc_files_mime"),
        Index("ix_sc_files_created", "created_at"),
    )


class SubmissionModel(WBScreenshotControlBase):
    """Скриншот с подписью от сотрудника: единица работы и KPI.

    Ключи кейса (`chat_id_norm`, `key_time`, `text_norm`, `article_norm`) — копия из распознавания
    для индексов; они же — задел под будущую сверку с WB. Имя покупателя хранится только HMAC.
    """

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    client_submission_id: Mapped[str] = mapped_column(String(80), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    install_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_media_group_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    client_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_tz_offset_min: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    client_meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    outcome: Mapped[str | None] = mapped_column(String(8), nullable=True)
    outcome_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    caption_raw: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outcome_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stage: Mapped[str] = mapped_column(String(10), nullable=False, server_default="processing")
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    case_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    chat_id_norm: Mapped[str | None] = mapped_column(CHAR(36), nullable=True)
    chat_id_source: Mapped[str | None] = mapped_column(String(8), nullable=True)
    key_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    key_time_precision: Mapped[str | None] = mapped_column(String(6), nullable=True)
    text_norm: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    text_len: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    text_key: Mapped[str | None] = mapped_column(CHAR(40), nullable=True)
    buyer_hmac: Mapped[str | None] = mapped_column(CHAR(16), nullable=True)
    article_norm: Mapped[str | None] = mapped_column(String(32), nullable=True)

    verdict: Mapped[str | None] = mapped_column(String(12), nullable=True)
    auto_verdict: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    audit_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    audit_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "client_submission_id", name="uq_sc_sub_client_id"),
        CheckConstraint(one_of("channel", CHANNELS), name="ck_sc_sub_channel"),
        CheckConstraint(one_of("outcome", OUTCOMES, nullable=True), name="ck_sc_sub_outcome"),
        CheckConstraint(one_of("outcome_source", OUTCOME_SOURCES, nullable=True), name="ck_sc_sub_outcome_source"),
        CheckConstraint(one_of("stage", STAGES), name="ck_sc_sub_stage"),
        CheckConstraint(one_of("case_kind", CASE_KINDS, nullable=True), name="ck_sc_sub_case_kind"),
        CheckConstraint(one_of("chat_id_source", CHAT_ID_SOURCES, nullable=True), name="ck_sc_sub_chat_source"),
        CheckConstraint(one_of("key_time_precision", TIME_PRECISIONS, nullable=True), name="ck_sc_sub_precision"),
        CheckConstraint(one_of("verdict", VERDICTS, nullable=True), name="ck_sc_sub_verdict"),
        CheckConstraint(one_of("auto_verdict", VERDICTS, nullable=True), name="ck_sc_sub_auto_verdict"),
        Index("ix_sc_sub_user_time", "user_id", text("received_at DESC")),
        Index("ix_sc_sub_stage", "stage", "received_at", postgresql_where=text("stage <> 'decided'")),
        Index("ix_sc_sub_chat", "chat_id_norm", postgresql_where=text("chat_id_norm IS NOT NULL")),
        Index("ix_sc_sub_key_time", "key_time", postgresql_where=text("key_time IS NOT NULL")),
        Index("ix_sc_sub_text_key", "text_key", postgresql_where=text("text_key IS NOT NULL")),
        Index("ix_sc_sub_file", "file_id"),
        Index("ix_sc_sub_review", "received_at", postgresql_where=text("verdict = 'needs_review'")),
        Index(
            "ix_sc_sub_audit",
            "received_at",
            postgresql_where=text("audit_requested AND audit_done_at IS NULL"),
        ),
        Index("ix_sc_sub_kpi", "user_id", "received_at", postgresql_where=text("verdict = 'accepted'")),
    )


class ExtractionModel(WBScreenshotControlBase):
    """Распознавание живёт у файла: один и тот же файл модель читает один раз на версию конвейера.

    `vlm_json` пишется уже без имени покупателя.
    """

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(16), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    layout: Mapped[str | None] = mapped_column(String(24), nullable=True)
    vlm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vlm_prompt: Mapped[str | None] = mapped_column(String(16), nullable=True)
    vlm_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ocr_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fields: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agreement: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("file_id", "pipeline_version", name="uq_sc_extractions_file_version"),
        CheckConstraint(one_of("status", EXTRACTION_STATUSES), name="ck_sc_extractions_status"),
    )


class VerdictEventModel(WBScreenshotControlBase):
    """Журнал вердиктов: только добавляется. `actor` — system, user:<uuid> или admin:<uuid>."""

    __tablename__ = "verdict_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    from_verdict: Mapped[str | None] = mapped_column(String(12), nullable=True)
    to_verdict: Mapped[str | None] = mapped_column(String(12), nullable=True)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    __table_args__ = (Index("ix_sc_events_sub", "submission_id", "at"),)


class StorageObjectModel(WBScreenshotControlBase):
    """Где лежат байты файла в S3 и в каком они состоянии; пишет и удаляет только подписчик Kafka.

    staged → uploading → stored → delete_requested → deleted; любое → failed после N попыток.
    """

    __tablename__ = "storage_objects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(5), nullable=False)
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(200), nullable=False)
    staging_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("file_id", "kind", name="uq_sc_objects_file_kind"),
        UniqueConstraint("bucket", "s3_key", name="uq_sc_objects_key"),
        CheckConstraint(one_of("kind", STORAGE_KINDS), name="ck_sc_objects_kind"),
        CheckConstraint(one_of("state", STORAGE_STATES), name="ck_sc_objects_state"),
        CheckConstraint("size_bytes > 0", name="ck_sc_objects_size"),
        Index("ix_sc_objects_state", "state", "updated_at"),
        Index("ix_sc_objects_stored", "stored_at"),
    )


class ExtensionInstallModel(WBScreenshotControlBase):
    """Расширение у сотрудника. Токен `mas_…` хранится хешем; блокировка сотрудника отсекает его сразу."""

    __tablename__ = "extension_installs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    install_id: Mapped[str] = mapped_column(String(64), nullable=False)
    browser: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    ext_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_sc_installs_user", "user_id", "revoked_at"),)


class PairingCodeModel(WBScreenshotControlBase):
    """Код из 8 символов на 15 минут: страница выдаёт, расширение обменивает на токен. Один живой на сотрудника."""

    __tablename__ = "pairing_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(CHAR(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    install_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("uq_sc_pairing_live", "code", unique=True, postgresql_where=text("used_at IS NULL")),
        Index("ix_sc_pairing_user", "user_id", "expires_at"),
    )


class TelegramLinkTokenModel(WBScreenshotControlBase):
    """Одноразовая ссылка t.me/<бот>?start=<токен>; в базе только хеш. Telegram — второй релиз."""

    __tablename__ = "telegram_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_sc_link_tokens_user", "user_id", "expires_at"),)


class TelegramLinkModel(WBScreenshotControlBase):
    """Привязка Telegram к сотруднику по неизменяемому id отправителя; username — только для показа."""

    __tablename__ = "telegram_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("uq_sc_tg_user_live", "telegram_user_id", unique=True, postgresql_where=text("revoked_at IS NULL")),
        Index("uq_sc_tg_staff_live", "user_id", unique=True, postgresql_where=text("revoked_at IS NULL")),
    )
