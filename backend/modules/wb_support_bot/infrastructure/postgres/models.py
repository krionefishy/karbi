import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, MetaData, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBSupportBotBase(DeclarativeBase):
    metadata = MetaData(schema="wb_support_bot")


MODE_DRY_RUN = "dry_run"
MODE_LIVE = "live"


class TrackedSellerModel(WBSupportBotBase):
    """Подключённые кабинеты и режим отправки.

    Подключение не включает отправку: кабинет стартует в `dry_run`, бот считает,
    кому и что отправил бы, и пишет это в журнал. В `live` его переводит человек.
    """

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=MODE_DRY_RUN)
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ControlModel(WBSupportBotBase):
    """Общий выключатель модуля: одна строка. Действует на все кабинеты сразу."""

    __tablename__ = "control"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TemplateModel(WBSupportBotBase):
    """Вариант сообщения напоминания. Набор свой у каждого кабинета; этапов нет, бот берёт случайный."""

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DialogModel(WBSupportBotBase):
    """Состояние диалога и расписание рассылки по нему.

    Диалог — отрезок чата от автосообщения WB. Состояние выводится из ленты при
    каждом проходе; здесь оно хранится, чтобы показывать страницу и вести
    расписание без пересчёта всей истории.
    """

    __tablename__ = "dialogs"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buyer_first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    buyer_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    our_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    campaign_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    campaigns_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    no_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    attention: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    tail_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tail_fingerprint: Mapped[str | None] = mapped_column(String(40), nullable=True)
    manual_verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)
    manual_fingerprint: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reply_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reply_cleared_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_first_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_wb_support_bot_dialogs_due", "seller_id", "state", "next_send_at"),
        Index("ix_wb_support_bot_dialogs_state", "seller_id", "state", "updated_at"),
    )


VERDICT_PENDING = "pending"
VERDICT_DONE = "done"
VERDICT_FAILED = "failed"


class VerdictModel(WBSupportBotBase):
    """Вердикт нейросети по хвосту диалога: кэш, журнал качества и учёт расхода.

    Текст сообщения здесь не хранится: он остаётся в зеркале чатов, строка знает
    только событие и отпечаток. Строка создаётся вместе с запросом в outbox.
    """

    __tablename__ = "verdicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tail_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tail_fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default=VERDICT_PENDING)
    verdict: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_wb_support_bot_verdicts_tail",
            "seller_id",
            "chat_id",
            "tail_fingerprint",
            "prompt_version",
            unique=True,
        ),
        Index("ix_wb_support_bot_verdicts_status", "status", "requested_at"),
    )


class ReplyVerdictModel(WBSupportBotBase):
    """Класс ответа покупателя на рассылку: по нему рассылка продолжается или останавливается.

    Строка описывает срез: подряд идущие сообщения покупателя, которые ещё никто не разобрал.
    Текст здесь не хранится, он остаётся в зеркале чатов. Решение «продолжаем / стоп»
    выводится из класса в коде (`CONTINUE_CATEGORIES`), в таблице хранится только класс.
    """

    __tablename__ = "reply_verdicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    slice_fingerprint: Mapped[str] = mapped_column(String(40), nullable=False)
    first_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    messages: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    prompt_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default=VERDICT_PENDING)
    category: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    guard: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_wb_support_bot_reply_verdicts_slice",
            "seller_id",
            "chat_id",
            "slice_fingerprint",
            "prompt_version",
            unique=True,
        ),
        Index("ix_wb_support_bot_reply_verdicts_time", "seller_id", "last_event_at"),
        Index("ix_wb_support_bot_reply_verdicts_category", "seller_id", "category", "last_event_at"),
        Index("ix_wb_support_bot_reply_verdicts_status", "status", "requested_at"),
    )


class ModeChangeModel(WBSupportBotBase):
    """История режимов кабинета: по ней на графике ответов ставится метка перехода на 100%."""

    __tablename__ = "mode_changes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_wb_support_bot_mode_changes_seller", "seller_id", "changed_at"),)


class MonitorStateModel(WBSupportBotBase):
    """Последний снимок проверок кабинета: время прохода воркера и результат каждой проверки."""

    __tablename__ = "monitor_state"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall: Mapped[str] = mapped_column(String(8), nullable=False)
    checks: Mapped[list] = mapped_column(JSONB, nullable=False)


class MonitorEventModel(WBSupportBotBase):
    """Период, пока проверка была жёлтой или красной: история для вкладки «Состояние»."""

    __tablename__ = "monitor_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    check_id: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_wb_support_bot_monitor_events_seller", "seller_id", "started_at"),
        Index("ix_wb_support_bot_monitor_events_open", "seller_id", "ended_at"),
    )


SEND_PLANNED = "planned"
SEND_SENDING = "sending"
SEND_SENT = "sent"
SEND_UNKNOWN = "unknown"
SEND_FAILED = "failed"
SEND_DRY_RUN = "dry_run"


class SendModel(WBSupportBotBase):
    """Журнал отправок: строка пишется до обращения к WB, исход дописывается после."""

    __tablename__ = "sends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    template_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("uq_wb_support_bot_sends_slot", "seller_id", "chat_id", "prompt_at", "seq", unique=True),
        Index("ix_wb_support_bot_sends_seller_time", "seller_id", "planned_at"),
    )


__all__ = [
    "MODE_DRY_RUN",
    "MODE_LIVE",
    "SEND_DRY_RUN",
    "SEND_FAILED",
    "SEND_PLANNED",
    "SEND_SENDING",
    "SEND_SENT",
    "SEND_UNKNOWN",
    "VERDICT_DONE",
    "VERDICT_FAILED",
    "VERDICT_PENDING",
    "ControlModel",
    "DialogModel",
    "ModeChangeModel",
    "MonitorEventModel",
    "MonitorStateModel",
    "ReplyVerdictModel",
    "SendModel",
    "TemplateModel",
    "TrackedSellerModel",
    "VerdictModel",
    "WBSupportBotBase",
]
