import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBReturnsBase(DeclarativeBase):
    metadata = MetaData(schema="wb_returns")


class TrackedSellerModel(WBReturnsBase):
    """Подключённые кабинеты и как прошёл последний сбор."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Попытка отмечается до сети: упавший процесс не должен превращаться в
    # кабинет, который спрашивают снова и снова.
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Архив заявок читается реже открытых: он большой и меняется только решениями.
    claims_archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReturnModel(WBReturnsBase):
    """Единица товара из отчёта «Возвраты и перемещения». Ключ — стикер (`shkId`).

    Статус меняется по дороге в ПВЗ; `status_changed_at` — когда сбор впервые
    увидел текущий статус, от него считаются напоминания.
    """

    __tablename__ = "returns"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    shk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sticker_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    srid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    brand: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tech_size: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    return_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status_key: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dst_office_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dst_office_address: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    order_dt: Mapped[date | None] = mapped_column(Date, nullable=True)
    ready_to_return_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_wb_returns_returns_active", "seller_id", "is_active", "status_key"),
        Index("ix_wb_returns_returns_changed", "seller_id", "status_changed_at"),
    )


class ClaimModel(WBReturnsBase):
    """Заявка покупателя на возврат. Ключ — UUID заявки в WB."""

    __tablename__ = "claims"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    claim_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_ex: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    imt_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    user_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    wb_comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    order_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dt_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    price: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    currency_code: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    srid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    photos: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    video_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    actions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_archive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_wb_returns_claims_open", "seller_id", "is_archive", "status", "dt"),)


class NotificationLogModel(WBReturnsBase):
    """Что уже ушло в бот: вид сообщения и ключ (стикер, заявка или дата дайджеста).

    Повторный проход воркера или рестарт не должны отправить то же ещё раз;
    outbox дедуплицирует доставку, а этот журнал — саму публикацию события.
    """

    __tablename__ = "notification_log"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshRequestModel(WBReturnsBase):
    """Нажатие «Обновить», которое ждёт воркера. Одно активное на кабинет."""

    __tablename__ = "refresh_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'success', 'error')", name="ck_wb_returns_refresh_status"),
        Index("ix_wb_returns_refresh_seller", "seller_id", "requested_at"),
        Index(
            "uq_wb_returns_refresh_active",
            "seller_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
