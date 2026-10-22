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
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBCardDocumentsBase(DeclarativeBase):
    metadata = MetaData(schema="wb_card_documents")


class TrackedSellerModel(WBCardDocumentsBase):
    """Sellers under watch, how the last collection went and whether the first one is behind.

    `baseline_at` is set by the first successful collection: before it every
    problem is «already there», after it a problem is news. `collection_error_permanent`
    tells a key WB will keep refusing from a failure worth retrying; the page banner reads it.
    `cursor_updated_at` and `cursor_nm_id` are the latest card seen, where the next
    changed-cards read starts (WB ignores a cursor without a real nmID);
    `full_collected_at` is the last full pass, the only one that sees cards leave.
    """

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)
    collection_error_permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    full_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CardStateModel(WBCardDocumentsBase):
    """The current state of each card: what we hold instead of the card itself.

    A card that left the catalog is closed (`closed_at`), not deleted, so the
    journal keeps pointing at something.
    """

    __tablename__ = "card_states"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    document_endless: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overall_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overall_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    overall_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    documents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    declared: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('ok', 'rejected', 'missing', 'expiring', 'mismatch')", name="ck_wb_card_documents_state"
        ),
    )


class TransitionModel(WBCardDocumentsBase):
    """Journal of state changes. `alerted` says whether the change went out as a message."""

    __tablename__ = "transitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    from_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_wb_card_documents_transitions_seller", "seller_id", "happened_at"),)


class NotificationLogModel(WBCardDocumentsBase):
    """What has already been said: digest per day, service alerts per episode, the first summary."""

    __tablename__ = "notification_log"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshRequestModel(WBCardDocumentsBase):
    """A «обновить сейчас» press; one active request per seller."""

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
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_card_documents_refresh_status"
        ),
        Index("ix_wb_card_documents_refresh_seller", "seller_id", "requested_at"),
        Index(
            "uq_wb_card_documents_refresh_active",
            "seller_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
