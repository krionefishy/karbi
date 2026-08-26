import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBFbsDistributionBase(DeclarativeBase):
    metadata = MetaData(schema="wb_fbs_distribution")


class TrackedSellerModel(WBFbsDistributionBase):
    """Селлеры, которым автоматизация считает распределение.

    `write_enabled` выключен по умолчанию осознанно: подключение кабинета не
    должно само по себе давать право переписывать его остатки в WB. Пока флаг
    снят, модуль только читает и считает план.
    """

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
