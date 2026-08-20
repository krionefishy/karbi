from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_turnover.infrastructure.wb.base import WBJsonClient

MARKETPLACE_BUCKET = "marketplace"
# WB accepts at most a thousand barcodes per stock request.
SKU_CHUNK = 1000


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: int
    name: str


class WBMarketplaceClient(WBJsonClient):
    """The FBS half of the stock: what the seller declared at his own warehouses."""

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    base_url = "https://marketplace-api.wildberries.ru"

    async def warehouses(self, api_key: str) -> list[Warehouse]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            payload = await self.request(client, "GET", f"{self.base_url}/api/v3/warehouses", api_key)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError("WB Marketplace API вернул не список складов")
        return [
            Warehouse(int(row["id"]), str(row.get("name") or ""))
            for row in payload
            if isinstance(row, dict) and isinstance(row.get("id"), int)
        ]

    async def stocks(self, api_key: str, warehouse_id: int, skus: list[str]) -> dict[str, int]:
        """Declared amount per barcode. Barcodes WB does not answer for count as zero."""
        collected: dict[str, int] = {}
        if not skus:
            return collected
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for offset in range(0, len(skus), SKU_CHUNK):
                chunk = skus[offset : offset + SKU_CHUNK]
                payload = await self.request(
                    client,
                    "POST",
                    f"{self.base_url}/api/v3/stocks/{warehouse_id}",
                    api_key,
                    json={"skus": chunk},
                )
                for row in self._stock_rows(payload):
                    collected[str(row["sku"])] = int(row.get("amount") or 0)
        return collected

    @staticmethod
    def _stock_rows(payload: Any) -> list[dict]:
        if payload is None:
            return []
        rows = payload.get("stocks") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise WBPermanentError("WB Marketplace API вернул неожиданный ответ по остаткам")
        return [row for row in rows if isinstance(row, dict) and row.get("sku")]
