import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Integer, String, any_, bindparam, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased
from sqlalchemy.sql import ColumnElement

from backend.modules.wb_core.domain import (
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    ChatEvent,
    FbsOrder,
    FbsSupply,
    ReviewFact,
    SellerWarehouse,
    StockFact,
    WbOffice,
)
from backend.modules.wb_core.infrastructure.postgres.models import (
    ChatCursorModel,
    ChatEventModel,
    FbsOrderArchiveMonthModel,
    FbsOrderModel,
    FbsSupplyModel,
    FbsWarehouseStockModel,
    MirrorStateModel,
    ReviewFactModel,
    SellerWarehouseModel,
    StockFactModel,
    WbOfficeModel,
)

_INSERT_CHUNK = 1000


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

    # --- catalog ----------------------------------------------------------------

    async def lock_catalog(self, seller_id: uuid.UUID) -> None:
        """Транзакционная блокировка на селлера: вторая запись каталога ждёт первую."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:seller_id))"), {"seller_id": str(seller_id)}
        )

    async def _insert(self, model: Any, rows: list[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), _INSERT_CHUNK):
            await self.session.execute(insert(model).values(rows[offset : offset + _INSERT_CHUNK]))


def _any_of(column: InstrumentedAttribute[Any], values: Iterable[Any], item_type: type) -> ColumnElement[bool]:
    """`column = ANY(:values)` вместо `IN (...)`: список уходит одним параметром-массивом.

    `IN` раскрывается в параметр на значение, а у asyncpg их не больше 32 767 —
    выгрузка штрафов за неделю спрашивает зеркало о десятках тысяч заданий.
    """
    return column == any_(bindparam(None, list(values), type_=ARRAY(item_type)))
