from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.modules.wb_core.domain.mirror import (
    ORDER_SOURCE_ARCHIVE,
    ORDER_SOURCE_LIVE,
    FbsOrder,
    FbsSupply,
    WbOffice,
)
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

MARKETPLACE_BUCKET = "marketplace"
# WB accepts at most a thousand identifiers per stock request.
CHRT_CHUNK = 1000
# Страница списков заданий и поставок; больше WB не отдаёт.
PAGE_LIMIT = 1000
# Живой список заданий принимает окно не больше 30 дней за запрос.
ORDERS_WINDOW = timedelta(days=30)
# Страховка от зацикленного курсора, а не ожидание: самый крупный кабинет
# делает ~1000 заданий в день, месяц — это ~30 страниц.
MAX_PAGES = 200


@dataclass(frozen=True, slots=True)
class Warehouse:
    id: int
    name: str
    office_id: int = 0


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
            Warehouse(int(row["id"]), str(row.get("name") or ""), int(row.get("officeId") or 0))
            for row in payload
            if isinstance(row, dict) and isinstance(row.get("id"), int) and not row.get("isDeleting")
        ]

    async def offices(self, seller_id: str) -> list[WbOffice]:
        """Справочник объектов WB. Общий для всех кабинетов, но спросить его можно только ключом."""
        payload = await self.request("GET", "/api/v3/offices", seller_id)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: справочник объектов пришёл не списком")
        return [
            WbOffice(
                office_id=int(row["id"]),
                name=str(row.get("name") or ""),
                city=str(row.get("city") or ""),
                address=str(row.get("address") or ""),
            )
            for row in payload
            if isinstance(row, dict) and isinstance(row.get("id"), int)
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

    # --- сборочные задания и поставки --------------------------------------------

    async def orders(self, seller_id: str, *, date_from: datetime, date_to: datetime) -> list[FbsOrder]:
        """Задания, созданные в окне. Окно длиннее 30 дней режется на куски: таков лимит WB.

        Живой список помнит только последние три месяца; что старше — в `archive_orders`.
        """
        collected: list[FbsOrder] = []
        for start, end in self._windows(date_from, date_to):
            collected.extend(
                await self._paged(
                    seller_id,
                    "/api/v3/orders",
                    {"dateFrom": int(start.timestamp()), "dateTo": int(end.timestamp())},
                    "orders",
                    self._live_order,
                )
            )
        return collected

    async def archive_orders(self, seller_id: str, year: int, month: int) -> list[FbsOrder]:
        """Задания старше трёх месяцев: у архива свой формат и месячная выборка."""
        return await self._paged(
            seller_id,
            "/api/marketplace/v3/fbs/orders/archive",
            {"year": year, "month": month},
            "orders",
            self._archive_order,
        )

    async def supplies(self, seller_id: str) -> list[FbsSupply]:
        """Все поставки кабинета: список идёт курсором от первой, без фильтра по дате."""
        return await self._paged(seller_id, "/api/v3/supplies", {}, "supplies", self._supply)

    async def _paged(self, seller_id: str, path: str, params: dict[str, Any], key: str, parse: Any) -> list:
        collected: list = []
        cursor = 0
        for _ in range(MAX_PAGES):
            payload = await self.request("GET", path, seller_id, params={**params, "limit": PAGE_LIMIT, "next": cursor})
            rows = self._list(payload, key)
            for raw in rows:
                parsed = parse(raw)
                if parsed is not None:
                    collected.append(parsed)
            following = payload.get("next") if isinstance(payload, dict) else None
            if not rows or not isinstance(following, int) or following in (0, cursor):
                return collected
            cursor = following
        raise WBPermanentError(f"{self.api_name}: {path} не закончился за {MAX_PAGES} страниц")

    def _list(self, payload: Any, key: str) -> list[dict]:
        if payload is None:
            return []
        rows = payload.get(key) if isinstance(payload, dict) else None
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: поле {key} пришло не списком")
        return [row for row in rows if isinstance(row, dict)]

    def _live_order(self, raw: dict) -> FbsOrder | None:
        order_id, created = raw.get("id"), self._moment(raw.get("createdAt"))
        if not isinstance(order_id, int) or created is None:
            self.logger.warning("wb_order_row_unusable", extra={"row": str(raw)[:200]})
            return None
        skus = raw.get("skus") or []
        return FbsOrder(
            order_id=order_id,
            rid=str(raw.get("rid") or ""),
            order_uid=str(raw.get("orderUid") or ""),
            created_at=created,
            warehouse_id=int(raw.get("warehouseId") or 0),
            supply_id=str(raw["supplyId"]) if raw.get("supplyId") else None,
            office_id=int(raw["officeId"]) if isinstance(raw.get("officeId"), int) else None,
            nm_id=int(raw.get("nmId") or 0),
            chrt_id=int(raw.get("chrtId") or 0),
            sku=str(skus[0]) if skus else "",
            price_kopecks=int(raw.get("price") or 0),
            sticker_id=None,
            supplier_status=None,
            wb_status=None,
            source=ORDER_SOURCE_LIVE,
        )

    def _archive_order(self, raw: dict) -> FbsOrder | None:
        order_id, created = raw.get("id"), self._moment(raw.get("createdAt"))
        if not isinstance(order_id, int) or created is None:
            self.logger.warning("wb_archive_order_row_unusable", extra={"row": str(raw)[:200]})
            return None
        product: dict = raw["product"] if isinstance(raw.get("product"), dict) else {}
        status: dict = raw["status"] if isinstance(raw.get("status"), dict) else {}
        price: dict = raw["priceInfo"] if isinstance(raw.get("priceInfo"), dict) else {}
        skus = product.get("skus") or []
        sticker = raw.get("stickerId")
        return FbsOrder(
            order_id=order_id,
            rid=str(raw.get("rid") or ""),
            order_uid=str(raw.get("orderUid") or ""),
            created_at=created,
            warehouse_id=int(raw.get("warehouseId") or 0),
            supply_id=str(raw["supplyId"]) if raw.get("supplyId") else None,
            office_id=None,
            nm_id=int(product.get("nmId") or 0),
            chrt_id=int(product.get("chrtId") or 0),
            sku=str(skus[0]) if skus else "",
            price_kopecks=int(price.get("price") or 0),
            sticker_id=int(sticker) if isinstance(sticker, int) and sticker else None,
            supplier_status=str(status["supplierStatus"]) if status.get("supplierStatus") else None,
            wb_status=str(status["wbStatus"]) if status.get("wbStatus") else None,
            source=ORDER_SOURCE_ARCHIVE,
        )

    def _supply(self, raw: dict) -> FbsSupply | None:
        supply_id, created = raw.get("id"), self._moment(raw.get("createdAt"))
        if not supply_id or created is None:
            return None
        destination = raw.get("destinationOfficeId")
        return FbsSupply(
            supply_id=str(supply_id),
            name=str(raw.get("name") or ""),
            created_at=created,
            closed_at=self._moment(raw.get("closedAt")),
            scan_dt=self._moment(raw.get("scanDt")),
            destination_office_id=int(destination) if isinstance(destination, int) and destination else None,
            done=bool(raw.get("done")),
            cargo_type=int(raw.get("cargoType") or 0),
        )

    @staticmethod
    def _windows(date_from: datetime, date_to: datetime) -> Iterator[tuple[datetime, datetime]]:
        start = date_from
        while start < date_to:
            end = min(start + ORDERS_WINDOW, date_to)
            yield start, end
            start = end

    @staticmethod
    def _moment(value: Any) -> datetime | None:
        """RFC 3339 с `Z` либо голая дата — архив отдаёт `createdAt` без времени."""
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    def _stock_rows(self, payload: Any) -> list[dict]:
        if payload is None:
            return []
        rows = payload.get("stocks") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: неожиданный ответ по остаткам")
        return [row for row in rows if isinstance(row, dict)]
