import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_stocks.domain import StockFact
from backend.modules.wb_fbs_stocks.infrastructure.postgres import FbsStocksRepository
from backend.modules.wb_fbs_stocks.infrastructure.wb import WBFbsStocksClient


@dataclass(frozen=True, slots=True)
class CollectionResult:
    warehouses: int
    polled: int
    barcodes: int
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Перечитать склады кабинета и остатки по списку баркодов селлера.

    Спрашиваются только склады, которые стоят в таблице: у кабинета бывает
    полсотни DBS-складов, и остаток на них никому не нужен. Сеть идёт до первой
    записи в базу: транзакция не должна висеть, пока шлюз разбирает очередь.
    """

    def __init__(self, session: AsyncSession, stocks: FbsStocksRepository, client: WBFbsStocksClient) -> None:
        self.session = session
        self.stocks = stocks
        self.client = client

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        """Временные ошибки WB уходят наверх: воркер повторит кабинет позже."""
        columns = await self.stocks.columns(seller_id)
        barcodes = [row.barcode for row in await self.stocks.barcodes(seller_id)]
        await self.session.commit()

        warehouses = await self.client.warehouses(str(seller_id))
        if not warehouses:
            # Кабинет без единого склада в этой автоматизации не бывает: пустой
            # ответ — сбой, и стирать по нему столбцы оператора нельзя.
            raise WBPermanentError("WB вернул пустой список складов кабинета")
        live = {warehouse.warehouse_id for warehouse in warehouses if not warehouse.is_deleting}
        wanted = [column.warehouse_id for column in columns if column.warehouse_id in live]
        facts: list[StockFact] = []
        for warehouse_id in wanted:
            amounts = await self.client.stocks(str(seller_id), warehouse_id, barcodes)
            # Строки нет — на этом складе остаток не заводили; для таблицы это ноль.
            facts.extend(
                StockFact(warehouse_id=warehouse_id, barcode=barcode, amount=amounts.get(barcode, 0))
                for barcode in barcodes
            )

        stamp = now or datetime.now(UTC)
        if not await self.stocks.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(0, 0, 0, skipped=True)
        await self.stocks.replace_warehouses(seller_id, warehouses)
        await self.stocks.replace_facts(seller_id, facts, now=stamp)
        await self.stocks.finish_collection(seller_id, None, now=stamp)
        await self.session.commit()
        return CollectionResult(warehouses=len(warehouses), polled=len(wanted), barcodes=len(barcodes))
