import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from backend.modules.wb_core.domain import WarehouseRemain
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError, WBTemporaryError
from backend.modules.wb_core.infrastructure.wb.egress import EgressGateway
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

ANALYTICS_BUCKET = "analytics"
# WB accepts this page size; every seller we have fits in a single page.
PAGE_LIMIT = 250_000
# A runaway-pagination backstop, not a real expectation.
MAX_PAGES = 100


@dataclass(frozen=True, slots=True)
class FBOStockRow:
    """One line of the WB warehouse stock report.

    `warehouse_id` / `warehouse_name` / `region_name` come back as a placeholder
    (`-999999`, «Склад WB») on every account we have checked — WB aggregates the
    report instead of splitting it by warehouse. The fields are kept because the
    report declares them, but nothing may be built on their values.
    """

    article: str
    chrt_id: int
    warehouse_id: int
    warehouse_name: str
    region_name: str
    quantity: int
    in_way_to_client: int
    in_way_from_client: int


class WBAnalyticsClient(WBJsonClient):
    """Current stock at Wildberries warehouses (FBO).

    Replaces `statistics /api/v1/supplier/stocks`, which WB retired: it now
    answers 404 «This method is deprecated».
    """

    bucket = ANALYTICS_BUCKET
    api_name = "WB Analytics API"
    category = "Аналитика"
    path = "/api/analytics/v1/stocks-report/wb-warehouses"

    async def stocks(self, seller_id: str) -> list[FBOStockRow]:
        """Every stock line of the account, one page after another.

        The report lists a товар only while it has stock or something in
        transit, so an article missing here is not «zero» — the caller fills
        those in from the catalog.
        """
        collected: list[FBOStockRow] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload = await self.request(
                "POST",
                self.path,
                seller_id,
                json={"limit": PAGE_LIMIT, "offset": offset},
            )
            items = self._items(payload)
            collected.extend(row for raw in items if (row := self._row(raw)) is not None)
            if len(items) < PAGE_LIMIT:
                return collected
            offset += len(items)
        raise WBPermanentError(f"{self.api_name}: отчёт по остаткам не закончился за {MAX_PAGES} страниц")

    def _items(self, payload: Any) -> list[dict]:
        if payload is None:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise WBPermanentError(f"{self.api_name}: в ответе нет объекта data")
        items = data.get("items")
        if items is None:
            return []
        if not isinstance(items, list):
            raise WBPermanentError(f"{self.api_name}: data.items не список")
        return [item for item in items if isinstance(item, dict)]

    def _row(self, raw: dict) -> FBOStockRow | None:
        article = raw.get("nmId")
        if not isinstance(article, int):
            # A line we cannot attribute to a товар is worse than no line.
            self.logger.warning("wb_analytics_row_without_nmid", extra={"row": str(raw)[:200]})
            return None
        return FBOStockRow(
            article=str(article),
            chrt_id=int(raw.get("chrtId") or 0),
            warehouse_id=int(raw.get("warehouseId") or 0),
            warehouse_name=str(raw.get("warehouseName") or ""),
            region_name=str(raw.get("regionName") or ""),
            quantity=int(raw.get("quantity") or 0),
            in_way_to_client=int(raw.get("inWayToClient") or 0),
            in_way_from_client=int(raw.get("inWayFromClient") or 0),
        )


class WBWarehouseRemainsClient(WBJsonClient):
    """Отчёт «Остатки на складах»: остаток каждого баркода по каждому складу WB.

    В отличие от `stocks-report/wb-warehouses`, этот отчёт склады не
    склеивает — по нему видно, сколько товара лежит в Коледино, а сколько в
    Краснодаре. Отчёт асинхронный: создать задачу, дождаться `done`, скачать.
    Лимиты WB — создание и скачивание раз в минуту, статус раз в пять секунд;
    очередь держит шлюз, здесь только пауза между опросами статуса.
    """

    bucket = ANALYTICS_BUCKET
    api_name = "WB Analytics API"
    category = "Аналитика"
    path = "/api/v1/warehouse_remains"

    def __init__(
        self,
        gateway: EgressGateway,
        *,
        priority: str = "background",
        poll_seconds: float = 5.0,
        max_wait_seconds: float = 300.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(gateway, priority=priority)
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self._sleep = sleep
        self._clock = clock

    async def remains(self, seller_id: str) -> list[WarehouseRemain]:
        created = await self.request(
            "GET",
            self.path,
            seller_id,
            params={"groupByBarcode": "true", "groupBySize": "true", "groupByNm": "true", "groupBySa": "true"},
        )
        task_id = self._task_id(created)
        # Ждём по часам, а не по сумме пауз: шлюз разводит запросы аналитики на
        # десятки секунд, и опрос статуса сам по себе идёт дольше паузы.
        started = self._clock()
        while True:
            status = await self.request("GET", f"{self.path}/tasks/{task_id}/status", seller_id)
            state = self._status(status)
            if state == "done":
                break
            if state in ("canceled", "purged"):
                raise WBPermanentError(f"{self.api_name}: отчёт об остатках не сформирован (статус {state})")
            if self._clock() - started >= self.max_wait_seconds:
                raise WBTemporaryError(
                    f"{self.api_name}: отчёт об остатках не готов за {int(self.max_wait_seconds)} с (статус {state})"
                )
            await self._sleep(self.poll_seconds)
        payload = await self.request("GET", f"{self.path}/tasks/{task_id}/download", seller_id)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: отчёт об остатках пришёл не списком")
        return [remain for raw in payload if isinstance(raw, dict) for remain in self._remains(raw)]

    def _task_id(self, payload: Any) -> str:
        data = payload.get("data") if isinstance(payload, dict) else None
        task_id = data.get("taskId") if isinstance(data, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise WBPermanentError(f"{self.api_name}: WB не вернул номер задачи отчёта об остатках")
        return task_id

    @staticmethod
    def _status(payload: Any) -> str:
        data = payload.get("data") if isinstance(payload, dict) else None
        return str(data.get("status") or "") if isinstance(data, dict) else ""

    def _remains(self, raw: dict) -> list[WarehouseRemain]:
        barcode = str(raw.get("barcode") or "")
        article = raw.get("nmId")
        if not barcode or not isinstance(article, int):
            # Строку без баркода не к чему привязать ни в заказах, ни в каталоге.
            self.logger.warning("wb_remains_row_without_barcode", extra={"row": str(raw)[:200]})
            return []
        warehouses = raw.get("warehouses")
        if not isinstance(warehouses, list):
            return []
        return [
            WarehouseRemain(
                barcode=barcode,
                article=str(article),
                tech_size=str(raw.get("techSize") or ""),
                vendor_code=str(raw.get("vendorCode") or ""),
                warehouse_name=str(item.get("warehouseName") or ""),
                quantity=int(item.get("quantity") or 0),
            )
            for item in warehouses
            if isinstance(item, dict) and item.get("warehouseName")
        ]
