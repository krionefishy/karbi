from dataclasses import dataclass
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError

MARKETPLACE_BUCKET = "marketplace"
# WB accepts at most a thousand identifiers per stock request.
CHRT_CHUNK = 1000


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: int
    name: str


class WBMarketplaceClient(WBJsonClient):
    """The FBS half of the stock: what the seller declared at his own warehouses."""

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    category = "Маркетплейс"

    async def warehouses(self, seller_id: str) -> list[Warehouse]:
        """Warehouses that can hold stock. Ones being deleted are skipped."""
        payload = await self.request("GET", "/api/v3/warehouses", seller_id)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: список складов пришёл не списком")
        return [
            Warehouse(int(row["id"]), str(row.get("name") or ""))
            for row in payload
            if isinstance(row, dict) and isinstance(row.get("id"), int) and not row.get("isDeleting")
        ]

    async def stocks(self, seller_id: str, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        """Declared amount per size.

        Asked by `chrtId`, not by barcode: the catalog already keys sizes that
        way, and one size can carry several barcodes. WB answers only for sizes
        that have stock, so a missing id means zero — the caller decides that,
        because «нет строки» and «ноль» must not be confused here either.
        """
        collected: dict[int, int] = {}
        if not chrt_ids:
            return collected
        for offset in range(0, len(chrt_ids), CHRT_CHUNK):
            chunk = chrt_ids[offset : offset + CHRT_CHUNK]
            payload = await self.request(
                "POST",
                f"/api/v3/stocks/{warehouse_id}",
                seller_id,
                json={"chrtIds": chunk},
            )
            for row in self._stock_rows(payload):
                chrt_id = row.get("chrtId")
                if isinstance(chrt_id, int):
                    collected[chrt_id] = int(row.get("amount") or 0)
        return collected

    def _stock_rows(self, payload: Any) -> list[dict]:
        if payload is None:
            return []
        rows = payload.get("stocks") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: неожиданный ответ по остаткам")
        return [row for row in rows if isinstance(row, dict)]
