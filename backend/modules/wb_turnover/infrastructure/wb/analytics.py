from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_turnover.infrastructure.wb.base import WBJsonClient

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
    base_url = "https://seller-analytics-api.wildberries.ru"
    path = "/api/analytics/v1/stocks-report/wb-warehouses"

    async def stocks(self, api_key: str) -> list[FBOStockRow]:
        """Every stock line of the account, one page after another.

        The report lists a товар only while it has stock or something in
        transit, so an article missing here is not «zero» — the caller fills
        those in from the catalog.
        """
        collected: list[FBOStockRow] = []
        offset = 0
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(MAX_PAGES):
                payload = await self.request(
                    client,
                    "POST",
                    f"{self.base_url}{self.path}",
                    api_key,
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
