import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
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
    # Участие и место в очереди ставит оператор, поэтому сверка с WB их не
    # трогает: она обновляет только то, что приехало из ответа.
    participates: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (Index("ix_wb_fbs_warehouses_office", "seller_id", "office_id"),)


class RegionModel(WBFbsDistributionBase):
    """Шесть логистических направлений: порядок приоритета и доля.

    Доля в сотых долях процента, а не дробью: расчёт целочисленный, и «сумма
    долей ровно 100%» проверяется точно, без накопленной ошибки float.
    """

    __tablename__ = "regions"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    share_bp: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (CheckConstraint("share_bp BETWEEN 0 AND 10000", name="ck_wb_fbs_region_share"),)


class OfficeRegionModel(WBFbsDistributionBase):
    """К какому направлению отнесён объект WB.

    Отдельной таблицей, а не колонкой в зеркале: зеркало перезаписывается
    ответом WB, а разметка принадлежит нам и переживать сверку обязана.
    """

    __tablename__ = "office_regions"

    office_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_wb_fbs_office_regions_region", "region_code"),)


class DistributionSettingsModel(WBFbsDistributionBase):
    """Числа расчёта, которые бизнес меняет без правки кода.

    Одна строка: пока политика общая для всех кабинетов и товаров. Когда
    понадобится своя на селлера или категорию, у таблицы появится область
    действия, а не вторая таблица рядом.
    """

    __tablename__ = "distribution_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Резерв на брак. Вычитается один раз на товар, до любого деления.
    reserve_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="20")
    # K для малого остатка. Считается в регионах, не в складах.
    priority_regions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_wb_fbs_settings_single_row"),
        CheckConstraint("reserve_units >= 0", name="ck_wb_fbs_settings_reserve"),
        CheckConstraint("priority_regions >= 1", name="ck_wb_fbs_settings_priority"),
    )


class StockSnapshotModel(WBFbsDistributionBase):
    """Журнал принятых и отклонённых выгрузок 1С.

    Отклонённые тоже хранятся: «сегодня расчёт не менялся» и «сегодня приехал
    битый файл» — разные события, и оператор должен видеть второе.
    """

    __tablename__ = "stock_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # Когда данные сформировала 1С, а не когда мы их получили: устаревание
    # считается от первого.
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('accepted', 'rejected')", name="ck_wb_fbs_snapshot_status"),
        Index("ix_wb_fbs_snapshots_received", "received_at"),
    )


class StockPoolModel(WBFbsDistributionBase):
    """Физический пул одной номенклатуры 1С.

    Ключ — номенклатура и характеристика: у товара с размерами каждый размер
    лежит и продаётся отдельно. Баркод хранится рядом, потому что именно им пул
    потом сопоставляется с размером карточки WB.
    """

    __tablename__ = "stock_pools"

    item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    characteristic: Mapped[str] = mapped_column(String(255), primary_key=True, server_default="")
    barcode: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    name: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Снимок, который последним назвал это число. Пул, которого в свежем снимке
    # не было, обнуляется, но строка остаётся: на ней держится сопоставление.
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_wb_fbs_pool_quantity"),
        Index("ix_wb_fbs_pools_barcode", "barcode"),
    )
