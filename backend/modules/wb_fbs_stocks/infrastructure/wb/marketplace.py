from collections.abc import Sequence
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_fbs_stocks.domain import SellerWarehouse

MARKETPLACE_BUCKET = "marketplace"
# WB принимает не больше тысячи позиций за запрос остатков.
SKU_CHUNK = 1000


class WBFbsStocksClient(WBJsonClient):
    """Склады кабинета и остатки на них. Только чтение.

    Свой клиент, а не импорт из распределения FBS: модули друг о друге не
    знают, и заморозка соседа не должна тянуть за собой эту таблицу.
    """

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    category = "Маркетплейс"

    async def warehouses(self, seller_id: str) -> list[SellerWarehouse]:
        payload = await self.request("GET", "/api/v3/warehouses", seller_id)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: список складов пришёл не списком")
        return [
            SellerWarehouse(
                warehouse_id=int(row["id"]),
                office_id=int(row.get("officeId") or 0),
                name=str(row.get("name") or "").strip(),
                delivery_type=int(row.get("deliveryType") or 0),
                is_deleting=bool(row.get("isDeleting")),
            )
            for row in payload
            if isinstance(row, dict) and isinstance(row.get("id"), int)
        ]

    async def stocks(self, seller_id: str, warehouse_id: int, skus: Sequence[str]) -> dict[str, int]:
        """Остаток по баркодам на одном складе.

        Строки нет — WB на этом складе такого остатка не заводил. Вызывающий
        превращает это в ноль сам: здесь «нет строки» и «ноль» ещё различимы.
        """
        collected: dict[str, int] = {}
        if not skus:
            return collected
        for offset in range(0, len(skus), SKU_CHUNK):
            payload = await self.request(
                "POST",
                f"/api/v3/stocks/{warehouse_id}",
                seller_id,
                json={"skus": list(skus[offset : offset + SKU_CHUNK])},
            )
            for row in self._rows(payload):
                if row.get("sku"):
                    collected[str(row["sku"])] = int(row.get("amount") or 0)
        return collected

    def _rows(self, payload: Any) -> list[dict]:
        if payload is None:
            return []
        rows = payload.get("stocks") if isinstance(payload, dict) else payload
        if rows is None:
            # `{"stocks": null}` — склад без единой записи, а не сломанный ответ.
            return []
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: неожиданный ответ по остаткам")
        return [row for row in rows if isinstance(row, dict)]
