import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import (
    MIRROR_CHATS,
    MIRROR_ORDERS,
    MIRROR_REMAINS,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    ChatEvent,
    FbsOrder,
    FbsSupply,
    ReviewFact,
    StockFact,
    WarehouseRemain,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository


class StockMirror:
    """Что автоматизации читают из зеркала остатков.

    `None` — зеркало до этого селлера ещё не доходило; пустой словарь — сбор
    стоит: данные старше `fresh_since` либо ни одна попытка не удалась.
    Потребитель различает «ещё не было» и «стоит», чтобы сказать оператору
    правду, а не обещать данные «в ближайший час» при отозванном ключе.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def stock(self, seller_id: uuid.UUID, *, fresh_since: datetime) -> dict[str, StockFact] | None:
        state = await self.mirror.state(seller_id, MIRROR_STOCKS)
        if state is None:
            return None
        if state.collected_at is None:
            return {} if state.error else None
        if state.collected_at < fresh_since:
            return {}
        return await self.mirror.stocks(seller_id)

    async def collected_at(self, seller_id: uuid.UUID) -> datetime | None:
        state = await self.mirror.state(seller_id, MIRROR_STOCKS)
        return state.collected_at if state else None


@dataclass(frozen=True, slots=True)
class RemainsSnapshot:
    """Отчёт по складам WB на момент сбора. `collected_at` None — не собирался ни разу."""

    remains: tuple[WarehouseRemain, ...]
    collected_at: datetime | None
    error: str | None


class RemainsMirror:
    """Остатки по складам WB из зеркала: что лежит и когда это сняли.

    Свежесть решает потребитель: подсорт показывает дату снимка рядом с
    цифрой, а не прячет цифру, собранную вчера.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def snapshot(self, seller_id: uuid.UUID) -> RemainsSnapshot:
        state = await self.mirror.state(seller_id, MIRROR_REMAINS)
        if state is None or state.collected_at is None:
            return RemainsSnapshot((), None, state.error if state else None)
        return RemainsSnapshot(tuple(await self.mirror.remains(seller_id)), state.collected_at, state.error)


class ReviewMirror:
    """Отзывы по карточкам из зеркала; `None` — ещё не доходили, пусто — сбор не удаётся."""

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def totals(self, seller_id: uuid.UUID) -> dict[str, ReviewFact] | None:
        state = await self.mirror.state(seller_id, MIRROR_REVIEWS)
        if state is None:
            return None
        if state.collected_at is None:
            return {} if state.error else None
        return await self.mirror.reviews(seller_id)

    async def collected_at(self, seller_id: uuid.UUID) -> datetime | None:
        state = await self.mirror.state(seller_id, MIRROR_REVIEWS)
        return state.collected_at if state else None


@dataclass(frozen=True, slots=True)
class OrderTrace:
    """Задание и его логистика: откуда ушло и в какой поставке уехало."""

    order: FbsOrder
    warehouse_name: str | None
    supply: FbsSupply | None
    destination_office_name: str | None


class OrderMirror:
    """Что автоматизации читают о заданиях FBS: по номеру — склад продавца и поставка.

    Ключи — `rid` (= `srid` отчётов), номер задания (`assembly_id`) и стикер;
    ответ ключуется теми же строками, что спросили, чтобы вызывающий не
    разбирал, какой ключ каким оказался.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def resolve(
        self,
        seller_id: uuid.UUID,
        *,
        rids: Iterable[str] = (),
        order_ids: Iterable[int] = (),
        sticker_ids: Iterable[int] = (),
    ) -> dict[str, OrderTrace]:
        orders = await self.mirror.orders_by_keys(seller_id, rids=rids, order_ids=order_ids, sticker_ids=sticker_ids)
        if not orders:
            return {}
        warehouses = await self.mirror.seller_warehouses(seller_id)
        supplies = await self.mirror.supplies_by_ids(seller_id, [order.supply_id or "" for order in orders])
        offices = await self.mirror.offices_by_ids([supply.destination_office_id or 0 for supply in supplies.values()])
        traces: dict[str, OrderTrace] = {}
        for order in orders:
            supply = supplies.get(order.supply_id or "")
            warehouse = warehouses.get(order.warehouse_id)
            office = offices.get(supply.destination_office_id or 0) if supply else None
            trace = OrderTrace(
                order=order,
                warehouse_name=warehouse.name if warehouse else None,
                supply=supply,
                destination_office_name=office.name if office else None,
            )
            for key in (order.rid, str(order.order_id), str(order.sticker_id or "")):
                if key:
                    traces[key] = trace
        return traces

    async def collected_at(self, seller_id: uuid.UUID) -> datetime | None:
        state = await self.mirror.state(seller_id, MIRROR_ORDERS)
        return state.collected_at if state else None


@dataclass(frozen=True, slots=True)
class ChatMirrorState:
    """Насколько ленте чатов можно верить.

    `read_through` — время последнего прочитанного события: до него лента
    известна, после — нет, даже если по часам это было давно. `synced_through`
    — когда в последний раз дочитали до конца; `None` при непустом
    `history_from` значит, что история ещё догоняется порциями. `error` —
    текст последней неудачи, прежние события при этом на месте.
    """

    history_from: datetime | None
    read_through: datetime | None
    synced_through: datetime | None
    error: str | None


class ChatMirror:
    """Что автоматизации читают о чатах с покупателями: диалоги после отзыва с низкой оценкой."""

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def review_dialog_events(self, seller_id: uuid.UUID, *, since: datetime, until: datetime) -> list[ChatEvent]:
        """События чатов, где автосообщение WB пришло в окне; по чату и времени, без верхней границы."""
        return await self.mirror.review_dialog_events(seller_id, since=since, until=until)

    async def launch_at(self, seller_id: uuid.UUID, *, within: timedelta) -> datetime | None:
        """Первое сообщение из API, ушедшее не позже `within` после автосообщения WB; `None` — не было."""
        return await self.mirror.first_api_reply_at(seller_id, within=within)

    async def state(self, seller_id: uuid.UUID) -> ChatMirrorState | None:
        """`None` — зеркало до этого селлера ещё не доходило."""
        state = await self.mirror.state(seller_id, MIRROR_CHATS)
        if state is None:
            return None
        cursor = await self.mirror.chat_cursor(seller_id)
        return ChatMirrorState(
            history_from=await self.mirror.first_chat_event_at(seller_id),
            read_through=datetime.fromtimestamp(cursor.next / 1000, tz=UTC) if cursor else None,
            synced_through=cursor.tail_reached_at if cursor else None,
            error=state.error,
        )
