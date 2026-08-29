from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError

MARKETPLACE_BUCKET = "marketplace"
# WB принимает не больше тысячи позиций за запрос остатков.
SKU_CHUNK = 1000


@dataclass(frozen=True, slots=True)
class Office:
    """Физический объект WB, куда продавец сдаёт готовые FBS-заказы.

    `cargo_type` решает, какой товар вообще можно через объект возить, поэтому
    он часть зеркала, а не украшение интерфейса.
    """

    office_id: int
    name: str
    city: str
    address: str
    federal_district: str
    longitude: float | None
    latitude: float | None
    cargo_type: int
    delivery_type: int
    # WB отмечает объекты, под которые у этого кабинета уже есть склад.
    selected: bool


@dataclass(frozen=True, slots=True)
class SellerWarehouse:
    """Виртуальный склад кабинета. `office_id` — куда он привязан."""

    warehouse_id: int
    office_id: int
    store_id: int | None
    name: str
    cargo_type: int
    delivery_type: int
    is_deleting: bool
    is_processing: bool


class WBFbsMarketplaceClient(WBJsonClient):
    """Чтение складской части кабинета: объекты WB и склады продавца.

    Только чтение. Создание, перепривязка и удаление складов живут отдельно:
    смешивать выборку справочника с командами, меняющими чужой кабинет, — самый
    короткий путь к тому, чтобы фоновая сверка что-нибудь переписала.
    """

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    category = "Маркетплейс"

    async def offices(self, seller_id: str) -> list[Office]:
        payload = await self.request("GET", "/api/v3/offices", seller_id)
        return [self._office(row) for row in self._rows(payload, "справочник объектов")]

    async def warehouses(self, seller_id: str) -> list[SellerWarehouse]:
        payload = await self.request("GET", "/api/v3/warehouses", seller_id)
        return [self._warehouse(row) for row in self._rows(payload, "список складов")]

    async def stocks(self, seller_id: str, warehouse_id: int, skus: Sequence[str]) -> dict[str, int]:
        """Что WB сейчас показывает на складе. Спрашивается по `sku`.

        Фильтр WB учитывает, поэтому спрашиваем ровно то, что публиковали.
        В ответе приходят и `sku`, и `chrtId`, но ключом остаётся `sku`: именно
        им остаток и пишется.

        Строки нет — значит ноль. Решает это вызывающий: «нет строки» и «ноль»
        путать здесь нельзя.
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
            rows = payload.get("stocks") if isinstance(payload, dict) else payload
            if rows is None:
                continue
            if not isinstance(rows, list):
                raise WBPermanentError(f"{self.api_name}: неожиданный ответ по остаткам")
            for row in rows:
                if isinstance(row, dict) and row.get("sku"):
                    collected[str(row["sku"])] = int(row.get("amount") or 0)
        return collected

    def _rows(self, payload: Any, what: str) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: {what} пришёл не списком")
        return [row for row in payload if isinstance(row, dict) and isinstance(row.get("id"), int)]

    def _office(self, row: dict) -> Office:
        return Office(
            office_id=int(row["id"]),
            name=str(row.get("name") or ""),
            city=str(row.get("city") or ""),
            address=str(row.get("address") or ""),
            # У части объектов WB округ пустой; пустая строка честнее выдуманного.
            federal_district=str(row.get("federalDistrict") or ""),
            longitude=self._coordinate(row.get("longitude")),
            latitude=self._coordinate(row.get("latitude")),
            cargo_type=int(row.get("cargoType") or 0),
            delivery_type=int(row.get("deliveryType") or 0),
            selected=bool(row.get("selected")),
        )

    def _warehouse(self, row: dict) -> SellerWarehouse:
        store_id = row.get("storeId")
        return SellerWarehouse(
            warehouse_id=int(row["id"]),
            office_id=int(row.get("officeId") or 0),
            store_id=int(store_id) if isinstance(store_id, int) else None,
            name=str(row.get("name") or ""),
            cargo_type=int(row.get("cargoType") or 0),
            delivery_type=int(row.get("deliveryType") or 0),
            is_deleting=bool(row.get("isDeleting")),
            is_processing=bool(row.get("isProcessing")),
        )

    @staticmethod
    def _coordinate(value: Any) -> float | None:
        return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
