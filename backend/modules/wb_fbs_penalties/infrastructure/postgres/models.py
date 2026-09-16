import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBFbsPenaltiesBase(DeclarativeBase):
    metadata = MetaData(schema="wb_fbs_penalties")


class TrackedSellerModel(WBFbsPenaltiesBase):
    """Подключённые кабинеты и как прошёл последний сбор отчёта."""

    __tablename__ = "tracked_sellers"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Попытка отмечается до сети: упавший процесс не должен превращаться в
    # кабинет, который спрашивают снова и снова.
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_error: Mapped[str | None] = mapped_column(String, nullable=True)


class ReportModel(WBFbsPenaltiesBase):
    """Отчёт реализации из списка WB и докуда дочитана его детализация.

    Отчёт после формирования не меняется, поэтому грузится один раз; `cursor` —
    `rrdId` последней прочитанной строки, `loaded_at` — детализация дочитана.
    """

    __tablename__ = "reports"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    create_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_type: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    penalty_sum: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    deduction_sum: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_wb_fbs_penalties_reports_period", "seller_id", "date_from"),)


class ReportRowModel(WBFbsPenaltiesBase):
    """Строка детализации отчёта реализации с удержанием. Ключ — `rrd_id` WB.

    Хранятся только строки, где хоть одна сумма ненулевая: продажи и логистика
    без удержаний этой автоматизации не нужны, а строк в отчёте тысячи в неделю.
    """

    __tablename__ = "report_rows"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    rrd_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    realizationreport_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    create_dt: Mapped[date | None] = mapped_column(Date, nullable=True)
    srid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    assembly_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sticker_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sale_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rr_dt: Mapped[date | None] = mapped_column(Date, nullable=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sa_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    barcode: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ts_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bonus_type_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    supplier_oper_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    delivery_method: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    office_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    penalty: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    deduction: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    rebill_logistic_cost: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    storage_fee: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    additional_payment: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    acceptance: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_wb_fbs_penalties_rows_period", "seller_id", "rr_dt"),
        Index("ix_wb_fbs_penalties_rows_sticker", "seller_id", "sticker_id"),
        Index("ix_wb_fbs_penalties_rows_assembly", "seller_id", "assembly_id"),
        Index("ix_wb_fbs_penalties_rows_srid", "seller_id", "srid"),
    )


class RefreshRequestModel(WBFbsPenaltiesBase):
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
        CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_fbs_penalties_refresh_status"
        ),
        Index("ix_wb_fbs_penalties_refresh_seller", "seller_id", "requested_at"),
        Index(
            "uq_wb_fbs_penalties_refresh_active",
            "seller_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
