import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBCoreBase(DeclarativeBase):
    metadata = MetaData(schema="wb_core")


class SellerModel(WBCoreBase):
    __tablename__ = "sellers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Archiving replaces deletion: the seller leaves every automation and the
    # collected history stays where it is.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    catalog_sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    last_catalog_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Сага доставки ключа на шлюз wb-egress: статус отвечает шлюз
    # (delivered/verified/key_invalid/no_free_ip/disabled), либо доставка не
    # прошла с нашей стороны (undelivered/unsynced). Ключа в этой базе нет.
    egress_status: Mapped[str] = mapped_column(String(16), nullable=False, default="undelivered")
    egress_error: Mapped[str | None] = mapped_column(String, nullable=True)
    egress_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Монотонная версия для идемпотентных upsert'ов шлюза: очередная версия —
    # max(wall-clock мс, предыдущая + 1), поэтому ни скачок NTP назад, ни две
    # правки в одну миллисекунду не дадут шлюзу принять позднее за раннее.
    egress_version: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (
        CheckConstraint(
            "catalog_sync_status IN ('queued', 'syncing', 'success', 'error')",
            name="ck_wb_core_sellers_catalog_sync_status",
        ),
        Index("ix_wb_core_sellers_archived", "archived_at"),
    )


class ArticleModel(WBCoreBase):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    imt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    brand: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    photo_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    sizes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # active — в продаже, archived — в корзине WB, feedback_only — карточки нет
    # ни в каталоге, ни в корзине, но по ней приходят отзывы.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'archived', 'feedback_only')",
            name="ck_wb_core_articles_state",
        ),
        UniqueConstraint("seller_id", "article", name="uq_wb_core_articles_seller_article"),
        Index("ix_wb_core_articles_seller_state", "seller_id", "state"),
        Index("ix_wb_core_articles_seller_imt", "seller_id", "imt_id"),
    )


class OutboxEventModel(WBCoreBase):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    message_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_wb_core_outbox_pending", "published_at", "next_attempt_at", "created_at"),)


class InboxEventModel(WBCoreBase):
    __tablename__ = "inbox_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
