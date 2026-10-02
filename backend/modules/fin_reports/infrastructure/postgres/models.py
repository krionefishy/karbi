import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Index, MetaData, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class FinReportsBase(DeclarativeBase):
    metadata = MetaData(schema="fin_reports")


class TrackedSellerModel(FinReportsBase):
    """Кабинеты, по которым строится отчёт. Сами деньги лежат в зеркале отчётов реализации wb_core."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CostPriceModel(FinReportsBase):
    """Себестоимость единицы артикула в кабинете с даты `effective_from`.

    Версии не переписываются: новая цена — новая строка со своей датой, а
    прошлые периоды считаются по той, что действовала тогда.
    """

    __tablename__ = "cost_prices"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(8), primary_key=True)
    article: Mapped[str] = mapped_column(String(64), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (
        CheckConstraint("marketplace IN ('wb', 'ozon')", name="ck_fin_reports_cost_prices_marketplace"),
        CheckConstraint("cost >= 0", name="ck_fin_reports_cost_prices_cost"),
        Index("ix_fin_reports_cost_prices_seller", "seller_id", "marketplace"),
    )
