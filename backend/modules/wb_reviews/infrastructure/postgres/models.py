import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, Integer, MetaData, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBReviewsBase(DeclarativeBase):
    metadata = MetaData(schema="wb_reviews")


class DailyReviewCountModel(WBReviewsBase):
    __tablename__ = "daily_review_counts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    count_reviews: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("count_reviews >= 0", name="ck_wb_reviews_daily_count_non_negative"),)
