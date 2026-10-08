import uuid
from datetime import date, datetime
from decimal import Decimal

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
    Text,
    func,
)
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


# Суммы за отчёт — рубли с копейками; запас по разрядам на годовые итоги крупного кабинета.
MONEY = Numeric(16, 2)


class WbReportModel(FinReportsBase):
    """Отчёт реализации WB, строки которого уже сложены в `wb_facts`.

    `version` — версия правила сложения: поменялся состав сумм — отчёты
    пересобираются, а не остаются наполовину старыми.
    """

    __tablename__ = "wb_reports"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_type: Mapped[int] = mapped_column(Integer, nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_fin_reports_wb_reports_dates", "seller_id", "date_from"),)


class WbFactModel(FinReportsBase):
    """Строки отчёта реализации, сложенные по артикулу, размеру, месяцу и виду операции.

    Отчёт WB неизменяем, поэтому складывается один раз, когда зеркало его
    дочитало: страница и выгрузка читают сотни готовых сумм, а не миллионы
    строк зеркала. Себестоимость сюда не входит — она меняется и применяется
    при чтении.
    """

    __tablename__ = "wb_facts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    report_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    month: Mapped[date | None] = mapped_column(Date, nullable=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    vendor_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tech_size: Mapped[str] = mapped_column(Text, nullable=False, default="")
    doc_type_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seller_oper_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bonus_type_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    srv_dbs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    return_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    retail_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    for_pay: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    delivery_service: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    rebill_logistic_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    penalty: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    additional_payment: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_storage: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    deduction: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_acceptance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_commission_change: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    acquiring_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    ppvz_reward: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    vw: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    vw_nds: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    ppvz_sales_commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)

    __table_args__ = (Index("ix_fin_reports_wb_facts_report", "seller_id", "report_id"),)


class WbStockSnapshotModel(FinReportsBase):
    """Остаток артикула на конец недели — снимок зеркала остатков wb_core.

    Зеркало хранит только текущий остаток, а листу нужен остаток «на последнюю
    неделю»: снимается в начале следующей недели и больше не меняется. Прошлые
    недели задним числом не восстановить — WB историю остатков не отдаёт.
    """

    __tablename__ = "wb_stock_snapshots"

    seller_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # Воскресенье недели, которую закрывает снимок.
    week_end: Mapped[date] = mapped_column(Date, primary_key=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tech_size: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    in_warehouse: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    to_client: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    from_client: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Остаток по отчёту аналитики WB — отдельный источник, в старом листе шёл своей колонкой.
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
