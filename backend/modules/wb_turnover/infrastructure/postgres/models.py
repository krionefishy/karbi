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
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DELIVERY_MODELS = ("fbo", "fbs")


class WBTurnoverBase(DeclarativeBase):
    metadata = MetaData(schema="wb_turnover")


class TrackedSellerModel(WBTurnoverBase):
    """Sellers this automation collects for."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Highest lastChangeDate we have pulled orders up to. Only moves on success,
    # so a failed run simply repeats itself instead of leaving a hole.
    orders_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warehouses_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SellerWarehouseModel(WBTurnoverBase):
    """A seller's own warehouses — the FBS side of the stock."""

    __tablename__ = "seller_warehouses"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockSnapshotModel(WBTurnoverBase):
    """Stock of one article at one moment of the day.

    Wildberries does not serve historical stock — every stock method is always
    "right now" — so the average stock over a period exists only if we keep
    taking these snapshots ourselves.
    """

    __tablename__ = "stock_snapshots"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True)
    # Index of the configured collection hour, so a repeated run overwrites
    # its own slot instead of inventing a new point in the average.
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_model: Mapped[str] = mapped_column(String(8), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_full: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_way_to_client: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_way_from_client: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("delivery_model IN ('fbo', 'fbs')", name="ck_wb_turnover_snapshot_model"),
        Index("ix_wb_turnover_snapshots_window", "seller_id", "snapshot_date"),
    )


class OrderModel(WBTurnoverBase):
    """One Wildberries order, kept as a row rather than a daily counter.

    Orders are pulled incrementally by `lastChangeDate`, and a cancellation
    arrives as the same order changed again. Counters could only be added to,
    so a replay would double-count and a cancellation would never subtract;
    keyed by `srid` the same row is simply overwritten.
    """

    __tablename__ = "orders"

    srid: Mapped[str] = mapped_column(String(128), primary_key=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    last_change_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_cancel: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    warehouse_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_wb_turnover_orders_window", "seller_id", "order_date"),
        Index("ix_wb_turnover_orders_article", "seller_id", "article", "order_date"),
    )


class TurnoverDailyModel(WBTurnoverBase):
    """The metric as computed on one day, kept so an alert can be explained later."""

    __tablename__ = "turnover_daily"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    stock_fbo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stock_fbs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stock_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_stock: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_daily_orders: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    # What the alert watches: current stock divided by the daily sales rate.
    days_of_cover: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    # The bookkeeping figure: average stock over the window, honest only once
    # the window is full of our own snapshots.
    turnover_days: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    stock_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sales_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ok")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'no_stock', 'no_sales', 'insufficient_data')",
            name="ck_wb_turnover_daily_status",
        ),
        Index("ix_wb_turnover_daily_alerts", "date", "status", "days_of_cover"),
    )


class CollectionRunModel(WBTurnoverBase):
    """One scheduled piece of work: a stock slot, an orders pull, the maths, the digest.

    The unique key is the schedule slot itself, so a worker restart cannot run
    the same slot twice and the whole thing needs no lease.
    """

    __tablename__ = "collection_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_sellers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("kind IN ('stocks', 'orders', 'calculation', 'digest')", name="ck_wb_turnover_runs_kind"),
        CheckConstraint(
            "status IN ('running', 'success', 'partial_success', 'error')", name="ck_wb_turnover_runs_status"
        ),
        UniqueConstraint("kind", "run_date", "slot", "trigger", name="uq_wb_turnover_run_slot"),
        Index("ix_wb_turnover_runs_recent", "started_at"),
    )


class NotificationLogModel(WBTurnoverBase):
    """What the digest told each seller on a given day."""

    __tablename__ = "notification_log"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    articles_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
