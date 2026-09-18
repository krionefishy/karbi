import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBReviewChatsBase(DeclarativeBase):
    metadata = MetaData(schema="wb_review_chats")


class TrackedSellerModel(WBReviewChatsBase):
    """Подключённые кабинеты. Своих данных у модуля нет: отчёт считается из зеркала чатов wb_core."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
