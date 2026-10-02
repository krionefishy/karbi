import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class WBCoreBase(DeclarativeBase):
    metadata = MetaData(schema="wb_core")


class SellerModel(WBCoreBase):
    __tablename__ = "sellers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Archiving replaces deletion: the seller disappears from every automation
    # until restored, and both the history and the enrollments stay where they are.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    catalog_sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    last_catalog_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    catalog_sync_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Сага доставки ключа на шлюз wb-egress: статус отвечает шлюз
    # (delivered/verified/key_invalid/no_free_ip/disabled), либо доставка не
    # прошла с нашей стороны (undelivered/unsynced). Ключа в этой базе нет.
    egress_status: Mapped[str] = mapped_column(String(16), nullable=False, default="undelivered")
    egress_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # То же самое для Ozon. Пара отдельная, потому что учётки независимы: ключ
    # WB может быть проверен, пока ключ Ozon отозван. Колонки без префикса
    # остались за Wildberries по истории — их читают фронтенд и сверка.
    ozon_egress_status: Mapped[str] = mapped_column(String(16), nullable=False, default="undelivered")
    ozon_egress_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # И для MPStats: это не маркетплейс, а аналитика по ним, но токен у селлера
    # свой и доставляется на шлюз той же сагой.
    mpstats_egress_status: Mapped[str] = mapped_column(String(16), nullable=False, default="undelivered")
    mpstats_egress_error: Mapped[str | None] = mapped_column(String, nullable=True)
    # Адрес общий: у селлера он один на оба маркетплейса.
    egress_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Монотонная версия для идемпотентных upsert'ов шлюза: очередная версия —
    # max(wall-clock мс, предыдущая + 1), поэтому ни скачок NTP назад, ни две
    # правки в одну миллисекунду не дадут шлюзу принять позднее за раннее.
    egress_version: Mapped[int] = mapped_column(BigInteger, default=0)

    __table_args__ = (
        CheckConstraint(
            "catalog_sync_status IN ('queued', 'syncing', 'success', 'error')",
            name="ck_wb_core_sellers_catalog_sync_status",
        ),
        Index("ix_wb_core_sellers_archived", "archived_at"),
    )


class ArticleModel(WBCoreBase):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    imt_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    brand: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    photo_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    # NULL — карточку ещё не читали каталогом; ноль — читали, и фото в ней нет.
    photo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sizes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # active — в продаже, archived — в корзине WB, feedback_only — карточки нет
    # ни в каталоге, ни в корзине, но по ней приходят отзывы.
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('active', 'archived', 'feedback_only')",
            name="ck_wb_core_articles_state",
        ),
        UniqueConstraint("seller_id", "article", name="uq_wb_core_articles_seller_article"),
        Index("ix_wb_core_articles_seller_state", "seller_id", "state"),
        Index("ix_wb_core_articles_seller_imt", "seller_id", "imt_id"),
    )


class OutboxEventModel(WBCoreBase):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    message_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_wb_core_outbox_pending", "published_at", "next_attempt_at", "created_at"),)


class InboxEventModel(WBCoreBase):
    __tablename__ = "inbox_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(255), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MirrorStateModel(WBCoreBase):
    """Как прошёл последний сбор зеркала по селлеру и виду данных.

    Строка на пару «селлер + вид», а не колонки в `sellers`: виды собираются
    по разному расписанию и падают независимо, а реестр про зеркало знать не
    обязан. Попытка отмечается до сети: упавший процесс не должен превращаться
    в селлера, которого спрашивают снова и снова.

    Ссылка на селлера с каскадом, как у `articles`: зеркало живёт в той же
    схеме, и удаление селлера убирает его вместе с каталогом.
    """

    __tablename__ = "mirror_state"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports')",
            name="ck_wb_core_mirror_state_kind",
        ),
    )


class StockFactModel(WBCoreBase):
    """Текущий остаток карточки — копия ответа WB, переписывается каждым сбором."""

    __tablename__ = "stock_facts"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    fbo_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fbo_quantity_full: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fbs_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FbsWarehouseStockModel(WBCoreBase):
    """Остаток FBS по складам продавца — тот же обход, что и сумма, но в разрезе."""

    __tablename__ = "fbs_warehouse_stocks"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WarehouseRemainModel(WBCoreBase):
    """Остаток баркода по складу WB из отчёта «Остатки на складах» — копия ответа, переписывается целиком.

    Служебные строки отчёта («В пути до получателей», «Всего находится на
    складах») лежат здесь же с тем именем, которое дал WB.
    """

    __tablename__ = "warehouse_remains"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    warehouse_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    article: Mapped[str] = mapped_column(String(255), nullable=False)
    tech_size: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    vendor_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewFactModel(WBCoreBase):
    """Отзывы карточки по звёздам на момент сбора. Медиа NULL — не считали."""

    __tablename__ = "review_facts"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    article: Mapped[str] = mapped_column(String(255), primary_key=True)
    count_rating_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_3: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_4: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_rating_5: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    count_with_photo: Mapped[int | None] = mapped_column(Integer, nullable=True)
    count_with_video: Mapped[int | None] = mapped_column(Integer, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SellerWarehouseModel(WBCoreBase):
    """Склады продавца в кабинете WB: имя к `warehouseId` задания. Переписывается каждым сбором."""

    __tablename__ = "seller_warehouses"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    warehouse_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    office_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WbOfficeModel(WBCoreBase):
    """Справочник объектов WB. Общий: у всех кабинетов один и тот же список."""

    __tablename__ = "wb_offices"

    office_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    address: Mapped[str] = mapped_column(Text, nullable=False, default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FbsOrderModel(WBCoreBase):
    """Сборочное задание FBS — копия ответа WB.

    Живой список отдаёт три месяца, архив — старше; поля, которых у живого нет
    (стикер, статусы), остаются NULL до архива. `supply_id` дозаполняется
    повторным чтением свежих заданий: в поставку их кладут не сразу.
    """

    __tablename__ = "fbs_orders"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    order_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    order_uid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    supply_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    office_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chrt_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sku: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    price_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sticker_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    supplier_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    wb_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("source IN ('live', 'archive')", name="ck_wb_core_fbs_orders_source"),
        Index("ix_wb_core_fbs_orders_rid", "seller_id", "rid"),
        Index("ix_wb_core_fbs_orders_sticker", "seller_id", "sticker_id"),
        Index("ix_wb_core_fbs_orders_created", "seller_id", "created_at"),
    )


class FbsOrderArchiveMonthModel(WBCoreBase):
    """Какие архивные месяцы заданий уже сняты: архив неизменяем, второй раз его не читают."""

    __tablename__ = "fbs_order_archive_months"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FbsSupplyModel(WBCoreBase):
    """Поставка FBS: чем и когда сдали задания на объект WB."""

    __tablename__ = "fbs_supplies"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    supply_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scan_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    destination_office_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cargo_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatEventModel(WBCoreBase):
    """Сообщение из ленты чатов с покупателями — копия ответа WB.

    Лента только дописывается, поэтому строки не обновляются. Текст покупателя
    NULL вне диалогов с автосообщением WB об отзыве: его нарочно не храним.
    """

    __tablename__ = "chat_events"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sender: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_new_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_wb_core_chat_events_chat", "seller_id", "chat_id", "added_at"),
        Index(
            "ix_wb_core_chat_events_prompts",
            "seller_id",
            "added_at",
            # `text` в теле класса — колонка сообщения, поэтому функция под другим именем.
            postgresql_where=sql_text("review_prompt"),
        ),
    )


class ChatCursorModel(WBCoreBase):
    """Докуда прочитана лента чатов селлера.

    `next` — курсор WB (время последнего события, мс). `tail_reached_at` — когда
    в последний раз дочитали до конца: пока история догоняется порциями, сбор
    уже «успешен», а отчётам верить ещё рано.
    """

    __tablename__ = "chat_cursors"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    next: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tail_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Деньги отчёта реализации: рубли с копейками, как их отдаёт WB.
MONEY = Numeric(14, 2)
# Проценты и коэффициенты WB бывают длиннее копеек.
RATIO = Numeric(14, 4)


class SalesReportModel(WBCoreBase):
    """Шапка отчёта реализации WB с итогами — копия строки списка отчётов.

    `rows_cursor` и `rows_loaded_at` — докуда дочитана детализация: страницы
    пишутся по одной, и сбой на второй не заставляет перечитывать первую.
    Отчёт WB не меняет, поэтому дочитанный больше не трогают.
    """

    __tablename__ = "sales_reports"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_type: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[str] = mapped_column(String(8), nullable=False)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    create_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    seller_finance_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    retail_amount_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    for_pay_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    delivery_service_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_storage_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_acceptance_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    deduction_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    penalty_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    additional_payment_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_amount_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_discount_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_commission_change_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    bank_payment_sum: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    rows_cursor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rows_loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("period IN ('weekly', 'daily')", name="ck_wb_core_sales_reports_period"),
        Index("ix_wb_core_sales_reports_period", "seller_id", "period", "date_from"),
    )


class SalesReportRowModel(WBCoreBase):
    """Строка детализации отчёта реализации — копия ответа WB, поля названы как у WB.

    Строка неизменяема; `rrdId` уникален в пределах кабинета. Суточные и
    недельные отчёты состоят из одних и тех же строк, поэтому ключ — без
    отчёта, а отчёт — колонкой. Строковые поля — без предела длины: слишком
    длинное название от WB не должно ронять вставку всей страницы.
    """

    __tablename__ = "sales_report_rows"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    rrd_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    report_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gi_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    doc_type_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seller_oper_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bonus_type_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    srid: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shk_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sticker_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    order_uid: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trbx_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    order_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sale_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rr_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fix_tariff_date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    fix_tariff_date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vendor_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    subject_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tech_size: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sku: Mapped[str] = mapped_column(Text, nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retail_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    retail_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    retail_price_withdisc: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    sale_percent: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    commission_percent: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    spp: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    product_discount_for_report: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    seller_promo: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    seller_promo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    seller_promo_discount: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    kvw_base: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    kvw: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    sup_rating_up: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    is_kgvp_v2: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    dlv_prc: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    ppvz_sales_commission: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    for_pay: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    ppvz_reward: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    acquiring_fee: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    acquiring_percent: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    acquiring_bank: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payment_processing: Mapped[str] = mapped_column(Text, nullable=False, default="")
    vw: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    vw_nds: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    delivery_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    return_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_service: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    rebill_logistic_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    rebill_logistic_org: Mapped[str] = mapped_column(Text, nullable=False, default="")
    penalty: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    additional_payment: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_storage: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    deduction: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    paid_acceptance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_discount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    cashback_commission_change: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    installment_cofinancing_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    wibes_discount_percent: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    loyalty_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    loyalty_discount: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    warehouse_logistics_coeff: Mapped[Decimal] = mapped_column(RATIO, nullable=False, default=0)
    payment_schedule: Mapped[str] = mapped_column(Text, nullable=False, default="")
    office_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ppvz_office_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ppvz_office_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    delivery_method: Mapped[str] = mapped_column(Text, nullable=False, default="")
    srv_dbs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_b2b: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    country: Mapped[str] = mapped_column(Text, nullable=False, default="")
    gi_box_type_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    declaration_number: Mapped[str] = mapped_column(Text, nullable=False, default="")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_wb_core_sales_report_rows_report", "seller_id", "report_id"),
        Index("ix_wb_core_sales_report_rows_nm", "seller_id", "nm_id", "rr_date"),
    )


class SellerTaxRateModel(WBCoreBase):
    """Ставка налога селлера в процентах с даты `effective_from`; версии не переписываются."""

    __tablename__ = "seller_tax_rates"

    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (CheckConstraint("rate >= 0 AND rate <= 100", name="ck_wb_core_seller_tax_rates_rate"),)
