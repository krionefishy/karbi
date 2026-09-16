import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import (
    MIRROR_ORDERS,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    FbsOrder,
    FbsSupply,
    ReviewFact,
    StockFact,
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
