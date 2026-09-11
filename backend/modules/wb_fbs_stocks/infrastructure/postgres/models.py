import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBFbsStocksBase(DeclarativeBase):
    metadata = MetaData(schema="wb_fbs_stocks")


class TrackedSellerModel(WBFbsStocksBase):
    """Подключённые кабинеты и как прошёл последний сбор."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Попытка отмечается до сети: упавший процесс не должен превращаться в
    # кабинет, который спрашивают снова и снова.
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)


class WarehouseModel(WBFbsStocksBase):
    """Зеркало складов кабинета. Переписывается каждым сбором.

    Склад, ушедший из кабинета, из зеркала уходит вместе со своим столбцом:
    показывать остаток на складе, которого нет, значит показывать вчерашний день.
    """

    __tablename__ = "warehouses"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    office_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    delivery_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GroupModel(WBFbsStocksBase):
    """Группа столбцов селлера: округ, фулфилмент или его собственные склады."""

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="district")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint("kind IN ('own', 'fulfilment', 'district')", name="ck_wb_fbs_stocks_group_kind"),
        Index("ix_wb_fbs_stocks_groups_seller", "seller_id", "position"),
    )


class ColumnModel(WBFbsStocksBase):
    """Склад в таблице и группа, в которой он стоит. Склад без строки здесь не показывается."""

    __tablename__ = "columns"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_fbs_stocks.groups.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_wb_fbs_stocks_columns_group", "group_id", "position"),)


class BarcodeModel(WBFbsStocksBase):
    """Строки таблицы: баркоды, которые селлер вписал сам, и заметка к каждому."""

    __tablename__ = "barcodes"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    note: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    note_updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StockFactModel(WBFbsStocksBase):
    """Остатки последнего сбора. Переписываются целиком по кабинету."""

    __tablename__ = "stock_facts"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RefreshRequestModel(WBFbsStocksBase):
    """Нажатие «Обновить», которое ждёт воркера. Одно активное на кабинет."""

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
        CheckConstraint("status IN ('queued', 'running', 'success', 'error')", name="ck_wb_fbs_stocks_refresh_status"),
        Index("ix_wb_fbs_stocks_refresh_seller", "seller_id", "requested_at"),
        Index(
            "uq_wb_fbs_stocks_refresh_active",
            "seller_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
