import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Integer, MetaData, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBPodsortBase(DeclarativeBase):
    metadata = MetaData(schema="wb_podsort")


class TrackedSellerModel(WBPodsortBase):
    """Кабинеты в подсорте и как прошла последняя догрузка заказов."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Последний проход, после которого все сутки истории на месте.
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)


class OrderDayModel(WBPodsortBase):
    """Сутки, заказы за которые уже сведены. День без заказов — тоже строка: иначе
    его спрашивали бы у WB снова и снова."""

    __tablename__ = "order_days"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OrderCountModel(WBPodsortBase):
    """Заказы баркода за сутки в регионе — итог, а не сами заказы."""

    __tablename__ = "order_counts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True)
    orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fbs_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BarcodeModel(WBPodsortBase):
    """Что WB назвал в заказе про баркод: карточка могла уйти из каталога, а заказы по ней остались."""

    __tablename__ = "barcodes"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tech_size: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SettingsModel(WBPodsortBase):
    """Параметры расчёта — одни на всех: сводный лист общий для кабинетов."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    window_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=7)
    cover_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=7)
    regions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (CheckConstraint("id = 1", name="ck_wb_podsort_settings_single"),)


class WarehouseRegionModel(WBPodsortBase):
    """Регион склада WB, заданный человеком. Без строки регион угадывается по городу в названии."""

    __tablename__ = "warehouse_regions"

    warehouse_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    # NULL — склад нарочно не относится ни к одному региону.
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
