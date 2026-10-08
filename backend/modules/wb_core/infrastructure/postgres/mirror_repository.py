import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    Integer,
    String,
    any_,
    bindparam,
    case,
    cast,
    delete,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased
from sqlalchemy.sql import ColumnElement

from backend.modules.wb_core.domain import (
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    SALES_RETURN_DOC_TYPE,
    AdvertCampaign,
    AdvertNmStat,
    AdvertSpend,
    ChatEvent,
    FbsOrder,
    FbsSupply,
    OzonAccrualLine,
    ReviewFact,
    SalesReport,
    SalesReportRow,
    SalesReportTotals,
    SellerWarehouse,
    StockFact,
    WarehouseRemain,
    WbOffice,
)
from backend.modules.wb_core.infrastructure.postgres.models import (
    AdvertCampaignModel,
    AdvertCursorModel,
    AdvertNmStatModel,
    AdvertSpendModel,
    ChatCursorModel,
    ChatEventModel,
    FbsOrderArchiveMonthModel,
    FbsOrderModel,
    FbsSupplyModel,
    FbsWarehouseStockModel,
    MirrorStateModel,
    OzonAccrualCursorModel,
    OzonAccrualLineModel,
    ReviewFactModel,
    SalesReportModel,
    SalesReportRowModel,
    SellerWarehouseModel,
    StockFactModel,
    WarehouseRemainModel,
    WbOfficeModel,
)

_INSERT_CHUNK = 1000
# asyncpg принимает не больше 32 767 параметров на запрос: у широкой таблицы
# порция строк считается от числа колонок, а не берётся круглой.
_MAX_QUERY_PARAMETERS = 32_767


def _chunk_size(rows: list[dict[str, Any]]) -> int:
    return max(1, min(_INSERT_CHUNK, _MAX_QUERY_PARAMETERS // max(len(rows[0]), 1))) if rows else _INSERT_CHUNK


class MirrorRepository:
    """Зеркало WB по селлеру: отметки сбора и сами факты."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- schedule -------------------------------------------------------------

    async def states(self, kind: str) -> dict[uuid.UUID, MirrorStateModel]:
        rows = await self.session.scalars(select(MirrorStateModel).where(MirrorStateModel.kind == kind))
        return {row.seller_id: row for row in rows}

    async def state(self, seller_id: uuid.UUID, kind: str) -> MirrorStateModel | None:
        return await self.session.get(MirrorStateModel, (seller_id, kind))

    async def sellers_due(
        self,
        kind: str,
        candidates: Iterable[uuid.UUID],
        *,
        since: datetime,
        retry_after: datetime,
    ) -> list[uuid.UUID]:
        """Кто из кандидатов не собран с `since` и не пробован с `retry_after`.

        Селлер без строки состояния — новый, и он в очереди сразу: после
        деплоя зеркало наполняется за один оборот, а не к следующему слоту.
        """
        states = await self.states(kind)
        due: list[uuid.UUID] = []
        for seller_id in candidates:
            row = states.get(seller_id)
            if row is None:
                due.append(seller_id)
                continue
            collected = row.collected_at is not None and row.collected_at >= since
            attempted = row.attempted_at is not None and row.attempted_at >= retry_after
            if not collected and not attempted:
                due.append(seller_id)
        return due

    async def record_attempt(self, seller_id: uuid.UUID, kind: str, *, now: datetime) -> None:
        statement = insert(MirrorStateModel).values(seller_id=seller_id, kind=kind, attempted_at=now)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "kind"], set_={"attempted_at": statement.excluded.attempted_at}
            )
        )

    async def mark_collected(self, seller_id: uuid.UUID, kind: str, *, now: datetime) -> None:
        await self.session.execute(
            update(MirrorStateModel)
            .where(MirrorStateModel.seller_id == seller_id, MirrorStateModel.kind == kind)
            .values(collected_at=now, error=None)
        )

    async def mark_failed(self, seller_id: uuid.UUID, kind: str, error: str) -> None:
        """Прежние факты и их дата остаются; меняется только текст ошибки."""
        await self.session.execute(
            update(MirrorStateModel)
            .where(MirrorStateModel.seller_id == seller_id, MirrorStateModel.kind == kind)
            .values(error=error[:1000])
        )

    # --- stocks -----------------------------------------------------------------

    async def replace_stocks(
        self,
        seller_id: uuid.UUID,
        facts: Iterable[StockFact],
        per_warehouse: Mapping[tuple[str, int], int],
        *,
        now: datetime,
    ) -> None:
        """Остатки целиком: карточка, пропавшая из ответа, пропадает и отсюда."""
        await self.session.execute(delete(StockFactModel).where(StockFactModel.seller_id == seller_id))
        await self.session.execute(delete(FbsWarehouseStockModel).where(FbsWarehouseStockModel.seller_id == seller_id))
        await self._insert(
            StockFactModel,
            [
                {
                    "seller_id": seller_id,
                    "article": fact.article,
                    "fbo_quantity": fact.fbo_quantity,
                    "fbo_quantity_full": fact.fbo_quantity_full,
                    "fbs_quantity": fact.fbs_quantity,
                    "collected_at": now,
                }
                for fact in facts
            ],
        )
        await self._insert(
            FbsWarehouseStockModel,
            [
                {
                    "seller_id": seller_id,
                    "article": article,
                    "warehouse_id": warehouse_id,
                    "quantity": quantity,
                    "collected_at": now,
                }
                for (article, warehouse_id), quantity in per_warehouse.items()
            ],
        )

    async def stocks(self, seller_id: uuid.UUID) -> dict[str, StockFact]:
        rows = await self.session.scalars(select(StockFactModel).where(StockFactModel.seller_id == seller_id))
        return {
            row.article: StockFact(
                article=row.article,
                fbo_quantity=row.fbo_quantity,
                fbo_quantity_full=row.fbo_quantity_full,
                fbs_quantity=row.fbs_quantity,
            )
            for row in rows
        }

    async def fbs_warehouse_stocks(self, seller_id: uuid.UUID) -> dict[tuple[str, int], int]:
        rows = await self.session.scalars(
            select(FbsWarehouseStockModel).where(FbsWarehouseStockModel.seller_id == seller_id)
        )
        return {(row.article, row.warehouse_id): row.quantity for row in rows}

    # --- reviews ----------------------------------------------------------------

    async def replace_remains(self, seller_id: uuid.UUID, remains: Iterable[WarehouseRemain], *, now: datetime) -> None:
        """Отчёт по складам целиком: баркод, пропавший из ответа, пропадает и отсюда.

        WB может повторить пару «баркод + склад» — например, у двух размеров с
        одним баркодом; такие строки складываются, а не спорят за ключ.
        """
        await self.session.execute(delete(WarehouseRemainModel).where(WarehouseRemainModel.seller_id == seller_id))
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for remain in remains:
            key = (remain.barcode, remain.warehouse_name)
            row = merged.get(key)
            if row is None:
                merged[key] = {
                    "seller_id": seller_id,
                    "barcode": remain.barcode,
                    "warehouse_name": remain.warehouse_name,
                    "article": remain.article,
                    "tech_size": remain.tech_size,
                    "vendor_code": remain.vendor_code,
                    "quantity": remain.quantity,
                    "collected_at": now,
                }
            else:
                row["quantity"] += remain.quantity
        await self._insert(WarehouseRemainModel, list(merged.values()))

    async def remains(self, seller_id: uuid.UUID) -> list[WarehouseRemain]:
        rows = await self.session.scalars(
            select(WarehouseRemainModel).where(WarehouseRemainModel.seller_id == seller_id)
        )
        return [
            WarehouseRemain(
                barcode=row.barcode,
                article=row.article,
                tech_size=row.tech_size,
                vendor_code=row.vendor_code,
                warehouse_name=row.warehouse_name,
                quantity=row.quantity,
            )
            for row in rows
        ]

    async def replace_reviews(self, seller_id: uuid.UUID, facts: Iterable[ReviewFact], *, now: datetime) -> None:
        await self.session.execute(delete(ReviewFactModel).where(ReviewFactModel.seller_id == seller_id))
        await self._insert(
            ReviewFactModel,
            [
                {
                    "seller_id": seller_id,
                    "article": fact.article,
                    "count_rating_1": fact.ratings[0],
                    "count_rating_2": fact.ratings[1],
                    "count_rating_3": fact.ratings[2],
                    "count_rating_4": fact.ratings[3],
                    "count_rating_5": fact.ratings[4],
                    "count_with_photo": fact.with_photo,
                    "count_with_video": fact.with_video,
                    "collected_at": now,
                }
                for fact in facts
            ],
        )

    async def reviews(self, seller_id: uuid.UUID) -> dict[str, ReviewFact]:
        rows = await self.session.scalars(select(ReviewFactModel).where(ReviewFactModel.seller_id == seller_id))
        return {
            row.article: ReviewFact(
                article=row.article,
                ratings=(
                    row.count_rating_1,
                    row.count_rating_2,
                    row.count_rating_3,
                    row.count_rating_4,
                    row.count_rating_5,
                ),
                with_photo=row.count_with_photo,
                with_video=row.count_with_video,
            )
            for row in rows
        }

    # --- склады продавца и объекты WB ---------------------------------------------

    async def replace_seller_warehouses(
        self, seller_id: uuid.UUID, warehouses: Iterable[SellerWarehouse], *, now: datetime
    ) -> None:
        await self.session.execute(delete(SellerWarehouseModel).where(SellerWarehouseModel.seller_id == seller_id))
        await self._insert(
            SellerWarehouseModel,
            [
                {
                    "seller_id": seller_id,
                    "warehouse_id": warehouse.warehouse_id,
                    "name": warehouse.name,
                    "office_id": warehouse.office_id,
                    "collected_at": now,
                }
                for warehouse in warehouses
            ],
        )

    async def seller_warehouses(self, seller_id: uuid.UUID) -> dict[int, SellerWarehouse]:
        rows = await self.session.scalars(
            select(SellerWarehouseModel).where(SellerWarehouseModel.seller_id == seller_id)
        )
        return {row.warehouse_id: SellerWarehouse(row.warehouse_id, row.name, row.office_id) for row in rows}

    async def upsert_offices(self, offices: Iterable[WbOffice], *, now: datetime) -> None:
        """Справочник общий, поэтому не переписывается целиком: объект, пропавший из
        ответа одного кабинета, у другого может быть ещё на месте."""
        rows = [
            {
                "office_id": office.office_id,
                "name": office.name,
                "city": office.city,
                "address": office.address,
                "collected_at": now,
            }
            for office in offices
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(WbOfficeModel).values(rows[offset : offset + _INSERT_CHUNK])
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["office_id"],
                    set_={
                        "name": statement.excluded.name,
                        "city": statement.excluded.city,
                        "address": statement.excluded.address,
                        "collected_at": statement.excluded.collected_at,
                    },
                )
            )

    async def offices_by_ids(self, office_ids: Iterable[int]) -> dict[int, WbOffice]:
        wanted = {office_id for office_id in office_ids if office_id}
        if not wanted:
            return {}
        rows = await self.session.scalars(
            select(WbOfficeModel).where(_any_of(WbOfficeModel.office_id, wanted, Integer))
        )
        return {row.office_id: WbOffice(row.office_id, row.name, row.city, row.address) for row in rows}

    # --- сборочные задания ----------------------------------------------------------

    async def upsert_orders(self, seller_id: uuid.UUID, orders: Iterable[FbsOrder], *, now: datetime) -> int:
        """Задание перечитывается, пока не попадёт в поставку: `supply_id` и то, что
        известно только архиву, не затираются пустотой."""
        rows = [
            {
                "seller_id": seller_id,
                "order_id": order.order_id,
                "rid": order.rid,
                "order_uid": order.order_uid,
                "created_at": order.created_at,
                "warehouse_id": order.warehouse_id,
                "supply_id": order.supply_id,
                "office_id": order.office_id,
                "nm_id": order.nm_id,
                "chrt_id": order.chrt_id,
                "sku": order.sku,
                "price_kopecks": order.price_kopecks,
                "sticker_id": order.sticker_id,
                "supplier_status": order.supplier_status,
                "wb_status": order.wb_status,
                "source": order.source,
                "collected_at": now,
            }
            for order in orders
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(FbsOrderModel).values(rows[offset : offset + _INSERT_CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "order_id"],
                    set_={
                        "rid": excluded.rid,
                        "order_uid": excluded.order_uid,
                        "created_at": excluded.created_at,
                        "warehouse_id": excluded.warehouse_id,
                        "supply_id": func.coalesce(excluded.supply_id, FbsOrderModel.supply_id),
                        "office_id": func.coalesce(excluded.office_id, FbsOrderModel.office_id),
                        "nm_id": excluded.nm_id,
                        "chrt_id": excluded.chrt_id,
                        "sku": excluded.sku,
                        "price_kopecks": excluded.price_kopecks,
                        "sticker_id": func.coalesce(excluded.sticker_id, FbsOrderModel.sticker_id),
                        "supplier_status": func.coalesce(excluded.supplier_status, FbsOrderModel.supplier_status),
                        "wb_status": func.coalesce(excluded.wb_status, FbsOrderModel.wb_status),
                        "source": excluded.source,
                        "collected_at": excluded.collected_at,
                    },
                )
            )
        return len(rows)

    async def orders_by_keys(
        self,
        seller_id: uuid.UUID,
        *,
        rids: Iterable[str] = (),
        order_ids: Iterable[int] = (),
        sticker_ids: Iterable[int] = (),
    ) -> list[FbsOrder]:
        conditions = []
        if rid_set := {rid for rid in rids if rid}:
            conditions.append(_any_of(FbsOrderModel.rid, rid_set, String))
        if id_set := {order_id for order_id in order_ids if order_id}:
            conditions.append(_any_of(FbsOrderModel.order_id, id_set, BigInteger))
        if sticker_set := {sticker for sticker in sticker_ids if sticker}:
            conditions.append(_any_of(FbsOrderModel.sticker_id, sticker_set, BigInteger))
        if not conditions:
            return []
        query = select(FbsOrderModel).where(FbsOrderModel.seller_id == seller_id)
        rows = await self.session.scalars(query.where(conditions[0] if len(conditions) == 1 else or_(*conditions)))
        return [self._order(row) for row in rows]

    async def latest_order_at(self, seller_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(
            select(func.max(FbsOrderModel.created_at)).where(FbsOrderModel.seller_id == seller_id)
        )

    async def archive_months_done(self, seller_id: uuid.UUID) -> set[tuple[int, int]]:
        rows = await self.session.execute(
            select(FbsOrderArchiveMonthModel.year, FbsOrderArchiveMonthModel.month).where(
                FbsOrderArchiveMonthModel.seller_id == seller_id
            )
        )
        return {(int(year), int(month)) for year, month in rows.all()}

    async def mark_archive_month(self, seller_id: uuid.UUID, year: int, month: int, *, now: datetime) -> None:
        statement = insert(FbsOrderArchiveMonthModel).values(
            seller_id=seller_id, year=year, month=month, collected_at=now
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "year", "month"], set_={"collected_at": statement.excluded.collected_at}
            )
        )

    async def prune_orders(self, before: datetime) -> int:
        result = await self.session.execute(delete(FbsOrderModel).where(FbsOrderModel.created_at < before))
        return int(getattr(result, "rowcount", 0) or 0)

    # --- поставки ---------------------------------------------------------------------

    async def upsert_supplies(self, seller_id: uuid.UUID, supplies: Iterable[FbsSupply], *, now: datetime) -> int:
        rows = [
            {
                "seller_id": seller_id,
                "supply_id": supply.supply_id,
                "name": supply.name,
                "created_at": supply.created_at,
                "closed_at": supply.closed_at,
                "scan_dt": supply.scan_dt,
                "destination_office_id": supply.destination_office_id,
                "done": supply.done,
                "cargo_type": supply.cargo_type,
                "collected_at": now,
            }
            for supply in supplies
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(FbsSupplyModel).values(rows[offset : offset + _INSERT_CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "supply_id"],
                    set_={
                        "name": excluded.name,
                        "created_at": excluded.created_at,
                        "closed_at": excluded.closed_at,
                        "scan_dt": excluded.scan_dt,
                        "destination_office_id": excluded.destination_office_id,
                        "done": excluded.done,
                        "cargo_type": excluded.cargo_type,
                        "collected_at": excluded.collected_at,
                    },
                )
            )
        return len(rows)

    async def supplies_by_ids(self, seller_id: uuid.UUID, supply_ids: Iterable[str]) -> dict[str, FbsSupply]:
        wanted = {supply_id for supply_id in supply_ids if supply_id}
        if not wanted:
            return {}
        rows = await self.session.scalars(
            select(FbsSupplyModel).where(
                FbsSupplyModel.seller_id == seller_id, _any_of(FbsSupplyModel.supply_id, wanted, String)
            )
        )
        return {
            row.supply_id: FbsSupply(
                supply_id=row.supply_id,
                name=row.name,
                created_at=row.created_at,
                closed_at=row.closed_at,
                scan_dt=row.scan_dt,
                destination_office_id=row.destination_office_id,
                done=row.done,
                cargo_type=row.cargo_type,
            )
            for row in rows
        }

    @staticmethod
    def _order(row: FbsOrderModel) -> FbsOrder:
        return FbsOrder(
            order_id=row.order_id,
            rid=row.rid,
            order_uid=row.order_uid,
            created_at=row.created_at,
            warehouse_id=row.warehouse_id,
            supply_id=row.supply_id,
            office_id=row.office_id,
            nm_id=row.nm_id,
            chrt_id=row.chrt_id,
            sku=row.sku,
            price_kopecks=row.price_kopecks,
            sticker_id=row.sticker_id,
            supplier_status=row.supplier_status,
            wb_status=row.wb_status,
            source=row.source,
        )

    # --- чаты с покупателями ------------------------------------------------------------

    async def chat_cursor(self, seller_id: uuid.UUID) -> ChatCursorModel | None:
        return await self.session.get(ChatCursorModel, seller_id)

    async def save_chat_cursor(self, seller_id: uuid.UUID, cursor: int, *, tail_reached_at: datetime | None) -> None:
        """Курсор двигается с каждой страницей; отметка «дочитали» — только когда дочитали."""
        statement = insert(ChatCursorModel).values(seller_id=seller_id, next=cursor, tail_reached_at=tail_reached_at)
        changes: dict[str, Any] = {"next": statement.excluded.next}
        if tail_reached_at is not None:
            changes["tail_reached_at"] = statement.excluded.tail_reached_at
        await self.session.execute(statement.on_conflict_do_update(index_elements=["seller_id"], set_=changes))

    async def chats_with_review_prompt(self, seller_id: uuid.UUID, chat_ids: Iterable[str]) -> set[str]:
        wanted = {chat_id for chat_id in chat_ids if chat_id}
        if not wanted:
            return set()
        rows = await self.session.scalars(
            select(ChatEventModel.chat_id)
            .where(
                ChatEventModel.seller_id == seller_id,
                ChatEventModel.review_prompt,
                _any_of(ChatEventModel.chat_id, wanted, String),
            )
            .distinct()
        )
        return set(rows)

    async def insert_chat_events(self, seller_id: uuid.UUID, events: Iterable[ChatEvent], *, now: datetime) -> None:
        """Лента неизменяема: событие, пришедшее второй раз на стыке страниц, не переписывается."""
        rows = [
            {
                "seller_id": seller_id,
                "event_id": event.event_id,
                "chat_id": event.chat_id,
                "sender": event.sender,
                "source": event.source,
                "added_at": event.added_at,
                "is_new_chat": event.is_new_chat,
                "review_prompt": event.review_prompt,
                "nm_id": event.nm_id,
                "rid": event.rid,
                "text": event.text,
                "has_attachments": event.has_attachments,
                "collected_at": now,
            }
            for event in events
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(ChatEventModel).values(rows[offset : offset + _INSERT_CHUNK])
            await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id", "event_id"]))

    async def review_dialog_events(self, seller_id: uuid.UUID, *, since: datetime, until: datetime) -> list[ChatEvent]:
        """Все события чатов, где автосообщение WB об отзыве пришло в окне, начиная с `since`.

        Верхней границы у событий нет: ответ на автосообщение последнего дня
        окна приходит уже за его пределами.
        """
        prompted = (
            select(ChatEventModel.chat_id)
            .where(
                ChatEventModel.seller_id == seller_id,
                ChatEventModel.review_prompt,
                ChatEventModel.added_at >= since,
                ChatEventModel.added_at < until,
            )
            .distinct()
        )
        rows = await self.session.scalars(
            select(ChatEventModel)
            .where(
                ChatEventModel.seller_id == seller_id,
                ChatEventModel.added_at >= since,
                ChatEventModel.chat_id.in_(prompted),
            )
            .order_by(ChatEventModel.chat_id, ChatEventModel.added_at, ChatEventModel.event_id)
        )
        return [self._chat_event(row) for row in rows]

    async def first_api_reply_at(self, seller_id: uuid.UUID, *, within: timedelta) -> datetime | None:
        """Первое сообщение из публичного API не позже `within` после автосообщения WB в том же чате.

        Просто «первое сообщение из API» не годится: менеджеры отвечают через
        сторонние клиенты часами позже, и такие одиночные ответы есть задолго
        до рассылки.
        """
        prompt = aliased(ChatEventModel)
        reply = aliased(ChatEventModel)
        return await self.session.scalar(
            select(func.min(reply.added_at))
            .select_from(reply)
            .join(
                prompt,
                (prompt.seller_id == reply.seller_id)
                & (prompt.chat_id == reply.chat_id)
                & prompt.review_prompt
                & (prompt.added_at < reply.added_at)
                & (reply.added_at <= prompt.added_at + within),
            )
            .where(
                reply.seller_id == seller_id,
                reply.sender == CHAT_SENDER_SELLER,
                reply.source == CHAT_SOURCE_API,
            )
        )

    async def first_chat_event_at(self, seller_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(
            select(func.min(ChatEventModel.added_at)).where(ChatEventModel.seller_id == seller_id)
        )

    async def prune_chat_events(self, before: datetime) -> int:
        result = await self.session.execute(delete(ChatEventModel).where(ChatEventModel.added_at < before))
        return int(getattr(result, "rowcount", 0) or 0)

    @staticmethod
    def _chat_event(row: ChatEventModel) -> ChatEvent:
        return ChatEvent(
            event_id=row.event_id,
            chat_id=row.chat_id,
            sender=row.sender,
            source=row.source,
            added_at=row.added_at,
            is_new_chat=row.is_new_chat,
            review_prompt=row.review_prompt,
            nm_id=row.nm_id,
            rid=row.rid,
            text=row.text,
            has_attachments=row.has_attachments,
        )

    # --- отчёты реализации --------------------------------------------------------------

    async def upsert_sales_reports(self, seller_id: uuid.UUID, reports: Iterable[SalesReport], *, now: datetime) -> int:
        """Шапки перечитываются каждым сбором; докуда дочитаны строки — не трогается."""
        rows = [{"seller_id": seller_id, "collected_at": now, **asdict(report)} for report in reports]
        chunk = _chunk_size(rows)
        for offset in range(0, len(rows), chunk):
            statement = insert(SalesReportModel).values(rows[offset : offset + chunk])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "report_id"],
                    set_={
                        column: getattr(excluded, column)
                        for column in rows[0]
                        if column not in ("seller_id", "report_id")
                    },
                )
            )
        return len(rows)

    async def pending_sales_reports(self, seller_id: uuid.UUID, *, limit: int) -> list[tuple[int, int]]:
        """Отчёты с недочитанными строками и курсор каждого, старшие первыми."""
        rows = await self.session.execute(
            select(SalesReportModel.report_id, SalesReportModel.rows_cursor)
            .where(SalesReportModel.seller_id == seller_id, SalesReportModel.rows_loaded_at.is_(None))
            .order_by(SalesReportModel.date_from, SalesReportModel.report_type, SalesReportModel.report_id)
            .limit(limit)
        )
        return [(int(report_id), int(cursor)) for report_id, cursor in rows.all()]

    async def insert_sales_report_rows(
        self, seller_id: uuid.UUID, rows: Iterable[SalesReportRow], *, now: datetime
    ) -> int:
        """Строка отчёта неизменяема: пришедшая второй раз (повтор страницы) не переписывается."""
        values = [{"seller_id": seller_id, "collected_at": now, **asdict(row)} for row in rows]
        chunk = _chunk_size(values)
        for offset in range(0, len(values), chunk):
            statement = insert(SalesReportRowModel).values(values[offset : offset + chunk])
            await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id", "rrd_id"]))
        return len(values)

    async def advance_sales_report(
        self, seller_id: uuid.UUID, report_id: int, *, cursor: int, loaded_at: datetime | None
    ) -> None:
        """Курсор двигается с каждой страницей; отметка «дочитан» — только на последней."""
        changes: dict[str, Any] = {"rows_cursor": cursor}
        if loaded_at is not None:
            changes["rows_loaded_at"] = loaded_at
        await self.session.execute(
            update(SalesReportModel)
            .where(SalesReportModel.seller_id == seller_id, SalesReportModel.report_id == report_id)
            .values(**changes)
        )

    async def sales_reports(
        self, seller_id: uuid.UUID, *, period: str, since: date, until: date
    ) -> list[tuple[SalesReport, datetime | None]]:
        """Шапки отчётов, чей период пересекает окно, с отметкой «строки дочитаны»."""
        rows = await self.session.scalars(
            select(SalesReportModel)
            .where(
                SalesReportModel.seller_id == seller_id,
                SalesReportModel.period == period,
                SalesReportModel.date_to >= since,
                SalesReportModel.date_from <= until,
            )
            .order_by(SalesReportModel.date_from, SalesReportModel.report_type, SalesReportModel.report_id)
        )
        return [(self._sales_report(row), row.rows_loaded_at) for row in rows]

    async def sales_report(self, seller_id: uuid.UUID, report_id: int) -> SalesReport | None:
        row = await self.session.get(SalesReportModel, (seller_id, report_id))
        return self._sales_report(row) if row else None

    async def sales_report_rows(self, seller_id: uuid.UUID, report_ids: Iterable[int]) -> list[SalesReportRow]:
        wanted = {report_id for report_id in report_ids if report_id}
        if not wanted:
            return []
        rows = await self.session.scalars(
            select(SalesReportRowModel)
            .where(
                SalesReportRowModel.seller_id == seller_id,
                _any_of(SalesReportRowModel.report_id, wanted, BigInteger),
            )
            .order_by(SalesReportRowModel.rrd_id)
        )
        return [self._sales_report_row(row) for row in rows]

    async def sales_report_totals(self, seller_id: uuid.UUID, report_ids: Iterable[int]) -> list[SalesReportTotals]:
        """Строки отчётов, сложенные по артикулу, размеру и виду операции.

        Год кабинета — миллионы строк, а разных ключей в отчёте — сотни:
        складывает база, наружу уходит только сумма.
        """
        wanted = {report_id for report_id in report_ids if report_id}
        if not wanted:
            return []
        model = SalesReportRowModel
        # Недельный отчёт на стыке месяцев WB делит не всегда: месяц операции — в ключе.
        month = cast(func.date_trunc("month", model.rr_date), Date).label("month")
        keys = (
            model.report_id,
            month,
            model.nm_id,
            model.tech_size,
            model.doc_type_name,
            model.seller_oper_name,
            model.bonus_type_name,
            model.srv_dbs,
        )
        sums = {name: func.coalesce(func.sum(getattr(model, name)), 0).label(name) for name in _TOTALS_SUMMED}
        result = await self.session.execute(
            select(
                *keys,
                func.max(model.vendor_code).label("vendor_code"),
                func.count().label("rows"),
                func.coalesce(func.sum(model.retail_price_withdisc * model.quantity), 0).label("gross"),
                *sums.values(),
            )
            .where(model.seller_id == seller_id, _any_of(model.report_id, wanted, BigInteger))
            .group_by(*keys)
            .order_by(*keys)
        )
        names = [field.name for field in fields(SalesReportTotals)]
        return [SalesReportTotals(**{name: getattr(row, name) for name in names}) for row in result.all()]

    async def sales_report_row_sums(self, seller_id: uuid.UUID, report_id: int) -> dict[str, Decimal]:
        """Суммы строк по тем колонкам, итоги которых WB кладёт в шапку.

        Возврат WB отдаёт положительной строкой с типом документа «Возврат»,
        а в итогах шапки вычитает — так же считаем и здесь.
        """
        sign = case((SalesReportRowModel.doc_type_name == SALES_RETURN_DOC_TYPE, -1), else_=1)
        columns = {
            "retail_amount_sum": SalesReportRowModel.retail_amount * sign,
            "for_pay_sum": SalesReportRowModel.for_pay * sign,
            "delivery_service_sum": SalesReportRowModel.delivery_service,
            "paid_storage_sum": SalesReportRowModel.paid_storage,
            "paid_acceptance_sum": SalesReportRowModel.paid_acceptance,
            "deduction_sum": SalesReportRowModel.deduction,
            "penalty_sum": SalesReportRowModel.penalty,
            "additional_payment_sum": SalesReportRowModel.additional_payment,
        }
        result = await self.session.execute(
            select(*(func.coalesce(func.sum(column), 0).label(name) for name, column in columns.items())).where(
                SalesReportRowModel.seller_id == seller_id, SalesReportRowModel.report_id == report_id
            )
        )
        row = result.one()
        return {name: Decimal(str(getattr(row, name))) for name in columns}

    @staticmethod
    def _sales_report(row: SalesReportModel) -> SalesReport:
        return SalesReport(**{field.name: getattr(row, field.name) for field in fields(SalesReport)})

    @staticmethod
    def _sales_report_row(row: SalesReportRowModel) -> SalesReportRow:
        return SalesReportRow(**{field.name: getattr(row, field.name) for field in fields(SalesReportRow)})

    # --- реклама ------------------------------------------------------------------------

    async def advert_cursor(self, seller_id: uuid.UUID) -> date | None:
        row = await self.session.get(AdvertCursorModel, seller_id)
        return row.collected_through if row else None

    async def save_advert_cursor(self, seller_id: uuid.UUID, collected_through: date) -> None:
        statement = insert(AdvertCursorModel).values(seller_id=seller_id, collected_through=collected_through)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id"], set_={"collected_through": statement.excluded.collected_through}
            )
        )

    async def upsert_advert_campaigns(
        self, seller_id: uuid.UUID, campaigns: Iterable[AdvertCampaign], *, now: datetime
    ) -> int:
        rows = [
            {
                "seller_id": seller_id,
                "advert_id": item.advert_id,
                "name": item.name,
                "status": item.status,
                "payment_type": item.payment_type,
                "bid_type": item.bid_type,
                "nm_ids": list(item.nm_ids),
                "updated_at": item.updated_at,
                "collected_at": now,
            }
            for item in campaigns
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(AdvertCampaignModel).values(rows[offset : offset + _INSERT_CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "advert_id"],
                    set_={
                        "name": excluded.name,
                        "status": excluded.status,
                        "payment_type": excluded.payment_type,
                        "bid_type": excluded.bid_type,
                        "nm_ids": excluded.nm_ids,
                        "updated_at": excluded.updated_at,
                        "collected_at": excluded.collected_at,
                    },
                )
            )
        return len(rows)

    async def replace_advert_spend(
        self, seller_id: uuid.UUID, since: date, until: date, spend: Iterable[AdvertSpend], *, now: datetime
    ) -> int:
        """Окно дней целиком: списание, пропавшее из ответа, пропадает и отсюда."""
        await self.session.execute(
            delete(AdvertSpendModel).where(
                AdvertSpendModel.seller_id == seller_id,
                AdvertSpendModel.day >= since,
                AdvertSpendModel.day <= until,
            )
        )
        rows = [
            {
                "seller_id": seller_id,
                "advert_id": item.advert_id,
                "day": item.day,
                "payment_type": item.payment_type,
                "amount": item.amount,
                "collected_at": now,
            }
            for item in spend
            if since <= item.day <= until
        ]
        await self._insert(AdvertSpendModel, rows)
        return len(rows)

    async def replace_advert_nm_stats(
        self,
        seller_id: uuid.UUID,
        advert_ids: Iterable[int],
        since: date,
        until: date,
        stats: Iterable[AdvertNmStat],
        *,
        now: datetime,
    ) -> int:
        """Окно дней для названных кампаний целиком — те же правила, что у списаний."""
        wanted = {advert_id for advert_id in advert_ids}
        if not wanted:
            return 0
        await self.session.execute(
            delete(AdvertNmStatModel).where(
                AdvertNmStatModel.seller_id == seller_id,
                _any_of(AdvertNmStatModel.advert_id, wanted, BigInteger),
                AdvertNmStatModel.day >= since,
                AdvertNmStatModel.day <= until,
            )
        )
        rows = [
            {
                "seller_id": seller_id,
                "advert_id": item.advert_id,
                "day": item.day,
                "nm_id": item.nm_id,
                "views": item.views,
                "clicks": item.clicks,
                "orders": item.orders,
                "shks": item.shks,
                "atbs": item.atbs,
                "canceled": item.canceled,
                "amount": item.amount,
                "orders_amount": item.orders_amount,
                "collected_at": now,
            }
            for item in stats
            if item.advert_id in wanted and since <= item.day <= until
        ]
        await self._insert(AdvertNmStatModel, rows)
        return len(rows)

    async def advert_spend(self, seller_id: uuid.UUID, *, since: date, until: date) -> list[AdvertSpend]:
        rows = await self.session.scalars(
            select(AdvertSpendModel).where(
                AdvertSpendModel.seller_id == seller_id, AdvertSpendModel.day >= since, AdvertSpendModel.day <= until
            )
        )
        return [AdvertSpend(row.advert_id, row.day, row.payment_type, row.amount) for row in rows]

    async def advert_nm_stats(self, seller_id: uuid.UUID, *, since: date, until: date) -> list[AdvertNmStat]:
        rows = await self.session.scalars(
            select(AdvertNmStatModel).where(
                AdvertNmStatModel.seller_id == seller_id,
                AdvertNmStatModel.day >= since,
                AdvertNmStatModel.day <= until,
            )
        )
        return [
            AdvertNmStat(
                row.advert_id,
                row.day,
                row.nm_id,
                row.views,
                row.clicks,
                row.orders,
                row.shks,
                row.atbs,
                row.canceled,
                row.amount,
                row.orders_amount,
            )
            for row in rows
        ]

    async def advert_campaigns(self, seller_id: uuid.UUID, advert_ids: Iterable[int]) -> dict[int, AdvertCampaign]:
        wanted = {advert_id for advert_id in advert_ids if advert_id}
        if not wanted:
            return {}
        rows = await self.session.scalars(
            select(AdvertCampaignModel).where(
                AdvertCampaignModel.seller_id == seller_id, _any_of(AdvertCampaignModel.advert_id, wanted, BigInteger)
            )
        )
        return {
            row.advert_id: AdvertCampaign(
                advert_id=row.advert_id,
                name=row.name,
                status=row.status,
                payment_type=row.payment_type,
                bid_type=row.bid_type,
                nm_ids=tuple(int(nm_id) for nm_id in row.nm_ids),
                updated_at=row.updated_at,
            )
            for row in rows
        }

    # --- начисления Ozon ------------------------------------------------------------------

    async def ozon_cursor(self, seller_id: uuid.UUID) -> date | None:
        row = await self.session.get(OzonAccrualCursorModel, seller_id)
        return row.collected_through if row else None

    async def save_ozon_cursor(self, seller_id: uuid.UUID, collected_through: date) -> None:
        statement = insert(OzonAccrualCursorModel).values(seller_id=seller_id, collected_through=collected_through)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id"], set_={"collected_through": statement.excluded.collected_through}
            )
        )

    async def replace_ozon_accruals(
        self, seller_id: uuid.UUID, day: date, lines: Iterable[OzonAccrualLine], *, now: datetime
    ) -> int:
        """День целиком: начисление, пропавшее из ответа, пропадает и отсюда."""
        await self.session.execute(
            delete(OzonAccrualLineModel).where(
                OzonAccrualLineModel.seller_id == seller_id, OzonAccrualLineModel.day == day
            )
        )
        rows = [{"seller_id": seller_id, "collected_at": now, **asdict(line)} for line in lines]
        chunk = _chunk_size(rows)
        for offset in range(0, len(rows), chunk):
            statement = insert(OzonAccrualLineModel).values(rows[offset : offset + chunk])
            # Одно начисление может прийти в двух днях (Ozon правит дату): побеждает свежее.
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "accrual_id", "line_no"],
                    set_={column: getattr(excluded, column) for column in rows[0] if column != "seller_id"},
                )
            )
        return len(rows)

    async def ozon_first_day(self, seller_id: uuid.UUID) -> date | None:
        return await self.session.scalar(
            select(func.min(OzonAccrualLineModel.day)).where(OzonAccrualLineModel.seller_id == seller_id)
        )

    async def ozon_accruals(self, seller_id: uuid.UUID, *, since: date, until: date) -> list[OzonAccrualLine]:
        rows = await self.session.scalars(
            select(OzonAccrualLineModel).where(
                OzonAccrualLineModel.seller_id == seller_id,
                OzonAccrualLineModel.day >= since,
                OzonAccrualLineModel.day <= until,
            )
        )
        names = [field.name for field in fields(OzonAccrualLine)]
        return [OzonAccrualLine(**{name: getattr(row, name) for name in names}) for row in rows]

    # --- catalog ----------------------------------------------------------------

    async def lock_catalog(self, seller_id: uuid.UUID) -> None:
        """Транзакционная блокировка на селлера: вторая запись каталога ждёт первую."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:seller_id))"), {"seller_id": str(seller_id)}
        )

    async def _insert(self, model: Any, rows: list[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), _INSERT_CHUNK):
            await self.session.execute(insert(model).values(rows[offset : offset + _INSERT_CHUNK]))


# Колонки строки отчёта, которые в сводке складываются как есть.
_TOTALS_SUMMED = (
    "quantity",
    "delivery_amount",
    "return_amount",
    "retail_amount",
    "for_pay",
    "delivery_service",
    "rebill_logistic_cost",
    "penalty",
    "additional_payment",
    "paid_storage",
    "deduction",
    "paid_acceptance",
    "cashback_amount",
    "cashback_discount",
    "cashback_commission_change",
    "acquiring_fee",
    "ppvz_reward",
    "vw",
    "vw_nds",
    "ppvz_sales_commission",
)


def _any_of(column: InstrumentedAttribute[Any], values: Iterable[Any], item_type: type) -> ColumnElement[bool]:
    """`column = ANY(:values)` вместо `IN (...)`: список уходит одним параметром-массивом.

    `IN` раскрывается в параметр на значение, а у asyncpg их не больше 32 767 —
    выгрузка штрафов за неделю спрашивает зеркало о десятках тысяч заданий.
    """
    return column == any_(bindparam(None, list(values), type_=ARRAY(item_type)))
