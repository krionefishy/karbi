import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBCardChecklistBase(DeclarativeBase):
    metadata = MetaData(schema="wb_card_checklist")


class TrackedSellerModel(WBCardChecklistBase):
    """Sellers this automation collects for, and how the last collection went."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Последний успешный сбор. Отметка на селлере, а не на строках карточек:
    # «карточек нет» и «ни разу не собирали» иначе не различить.
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Попытка отмечается до сети: упавший процесс не должен превращаться в
    # кабинет, который спрашивают снова и снова.
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Ошибка последней попытки или предупреждение успешной (цены не прочитаны).
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)


class CardFactsModel(WBCardChecklistBase):
    """The seller's cards as the content API returned them on the last collection.

    Rewritten whole each time: a card that left the catalog must leave the
    checklist too, and an upsert would keep it forever.
    """

    __tablename__ = "card_facts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    barcode: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    imt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    photo_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    has_video: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Только id заполненных характеристик: значения чек-листу не нужны, а
    # хранить их — значит копировать карточку целиком.
    characteristic_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    card_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SubjectCharacteristicsModel(WBCardChecklistBase):
    """The characteristics directory of one WB subject.

    Shared by every seller: the directory is WB's, not the cabinet's, so one
    seller's key reads it for all. It changes rarely and is re-read on a TTL.
    """

    __tablename__ = "subject_characteristics"

    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    characteristics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class PriceFactsModel(WBCardChecklistBase):
    __tablename__ = "price_facts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discounted_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    club_discount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MarkModel(WBCardChecklistBase):
    """A manager's tick on one item of one card.

    Keyed by the article, not by a checklist row: a товар that dips under the
    stock threshold drops out of the table and comes back with its ticks.
    """

    __tablename__ = "marks"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    item: Mapped[str] = mapped_column(String(64), primary_key=True)
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CommentModel(WBCardChecklistBase):
    __tablename__ = "comments"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    text: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshRequestModel(WBCardChecklistBase):
    """A «обновить сейчас» press, waiting for the worker to pick it up.

    The API never talks to Wildberries itself — it only records the wish. One
    active request per seller: pressing twice must not double the WB load.
    """

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
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_card_checklist_refresh_status"
        ),
        Index("ix_wb_card_checklist_refresh_seller", "seller_id", "requested_at"),
        Index(
            "uq_wb_card_checklist_refresh_active",
            "seller_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
