import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBReviewsBase(DeclarativeBase):
    metadata = MetaData(schema="wb_reviews")


class TrackedSellerModel(WBReviewsBase):
    """Sellers this automation collects for.

    Before this table membership was implicit — the run covered every seller in
    the registry — so the only way to leave the automation was to delete the
    seller himself.
    """

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class DailyReviewCountModel(WBReviewsBase):
    __tablename__ = "daily_review_counts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    count_rating_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_5: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "count_rating_1 >= 0 AND count_rating_2 >= 0 AND count_rating_3 >= 0 "
            "AND count_rating_4 >= 0 AND count_rating_5 >= 0",
            name="ck_wb_reviews_daily_counts_non_negative",
        ),
    )


class ReviewSyncRunModel(WBReviewsBase):
    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    total_sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="ck_wb_reviews_sync_runs_trigger"),
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'partial_success', 'error')",
            name="ck_wb_reviews_sync_runs_status",
        ),
        Index("ix_wb_reviews_sync_runs_created", "created_at"),
    )


class ReviewSyncJobModel(WBReviewsBase):
    __tablename__ = "sync_run_sellers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_reviews.sync_runs.id", ondelete="CASCADE"), nullable=False
    )
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    product_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set only while the job waits for a retry; a dispatched job carries NULL so
    # the reaper cannot hand the same work out twice.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')",
            name="ck_wb_reviews_sync_run_sellers_status",
        ),
        UniqueConstraint("run_id", "seller_id", name="uq_wb_reviews_sync_run_seller"),
        Index("ix_wb_reviews_sync_run_sellers_run_status", "run_id", "status"),
        Index("ix_wb_reviews_sync_run_sellers_due", "status", "next_attempt_at"),
        Index("ix_wb_reviews_sync_run_sellers_lease", "status", "lease_expires_at"),
    )
