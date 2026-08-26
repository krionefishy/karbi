import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Integer, MetaData, String, Text, func
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
    warehouses_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WBOfficeModel(WBFbsDistributionBase):
    """Зеркало справочника объектов WB.

    Справочник у всех кабинетов один, поэтому таблица общая, а не по селлеру:
    ключ нужен только чтобы его прочитать. Свежесть каталога — максимум
    `synced_at`, отдельного счётчика для этого не нужно.

    `selected` не храним: он значит «у этого кабинета уже есть склад под этот
    объект», то есть зависит от ключа, которым спросили, и выводится из
    `seller_warehouses`.
    """

    __tablename__ = "wb_offices"

    office_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    federal_district: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 1 — обычный груз, 3 — КГТ. Товар несовместимого габарита через объект не
    # повезти, поэтому тип груза участвует в отборе допустимых складов.
    cargo_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_wb_fbs_offices_city", "city"),)


class SellerWarehouseModel(WBFbsDistributionBase):
    """Зеркало виртуальных складов кабинета.

    Ключ — `(seller_id, warehouse_id)`, а не имя: имя в кабинете меняют руками,
    и сверка по нему завела бы дубликаты при первом же переименовании.
    """

    __tablename__ = "seller_warehouses"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    office_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    store_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    cargo_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Пока WB обрабатывает создание или удаление, склад не готов принимать
    # остатки, и публикация обязана его пропускать.
    is_deleting: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_processing: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (Index("ix_wb_fbs_warehouses_office", "seller_id", "office_id"),)
