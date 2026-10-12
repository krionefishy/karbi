import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, MetaData, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
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
    # Причина остановки отправки по кабинету (токен без записи и т. п.); None — всё в порядке.
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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_wb_support_bot_dialogs_due", "seller_id", "state", "next_send_at"),
        Index("ix_wb_support_bot_dialogs_state", "seller_id", "state", "updated_at"),
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
        # Номер отправки внутри диалога уникален: повторный тик не создаст вторую строку на тот же слот.
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
    "ControlModel",
    "DialogModel",
    "SendModel",
    "TemplateModel",
    "TrackedSellerModel",
    "WBSupportBotBase",
]
