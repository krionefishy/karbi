from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_turnover.infrastructure.wb.base import WBJsonClient

STATISTICS_BUCKET = "statistics"
# `dateFrom` filters by lastChangeDate and is mandatory; the stock method has no
# history to give, so the earliest date WB accepts simply means "everything".
ALL_TIME = "2019-06-20T00:00:00"
# WB truncates a large orders response instead of failing it, so the method is
# read in pages. The ceiling only guards against a page that never stops
# bringing something new; a normal pull ends on the first repeated page.
ORDERS_MAX_PAGES = 30


@dataclass(frozen=True, slots=True)
class StockRow:
    article: str
    barcode: str
    warehouse_name: str
    quantity: int
    quantity_full: int
    in_way_to_client: int
    in_way_from_client: int


@dataclass(frozen=True, slots=True)
class OrderRow:
    srid: str
    article: str
    order_date: date
    last_change_date: datetime
    is_cancel: bool
    price: float
    warehouse_type: str


class WBStatisticsClient(WBJsonClient):
    """Stocks and orders. The tightest category WB has, and the only source of both."""

    bucket = STATISTICS_BUCKET
    api_name = "WB Statistics API"
    base_url = "https://statistics-api.wildberries.ru"

    async def stocks(self, api_key: str) -> list[StockRow]:
        """Everything the account holds at WB warehouses right now.

        This is FBO only — stock at the seller's own warehouses lives behind the
        Marketplace API and is collected separately.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            payload = await self.request(
                client,
                "GET",
                f"{self.base_url}/api/v1/supplier/stocks",
                api_key,
                params={"dateFrom": ALL_TIME},
            )
        return [row for raw in self._rows(payload) if (row := self._stock(raw)) is not None]

    async def orders(self, api_key: str, date_from: datetime) -> list[OrderRow]:
        """Orders whose lastChangeDate is at or after `date_from`.

        Cancellations come back through this same window: a cancelled order is
        an order that changed, so following lastChangeDate keeps counts honest
        without re-reading everything.

        One GET is not the whole answer — WB silently truncates a big response.
        The next page starts from the lastChangeDate of the last row received
        (inclusive, so the boundary row repeats and is deduplicated by srid),
        and the pull stops once a page brings nothing new.
        """
        collected: dict[str, OrderRow] = {}
        cursor = date_from
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for _ in range(ORDERS_MAX_PAGES):
                payload = await self.request(
                    client,
                    "GET",
                    f"{self.base_url}/api/v1/supplier/orders",
                    api_key,
                    params={"dateFrom": cursor.strftime("%Y-%m-%dT%H:%M:%S"), "flag": 0},
                )
                rows = [row for raw in self._rows(payload) if (row := self._order(raw)) is not None]
                fresh = [row for row in rows if row.srid not in collected]
                if not fresh:
                    break
                for row in fresh:
                    collected[row.srid] = row
                cursor = max(row.last_change_date for row in rows)
        return list(collected.values())

    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError("WB Statistics API вернул не список")
        return [row for row in payload if isinstance(row, dict)]

    @staticmethod
    def _stock(raw: dict) -> StockRow | None:
        article = raw.get("nmId")
        if not isinstance(article, int):
            return None
        return StockRow(
            article=str(article),
            barcode=str(raw.get("barcode") or ""),
            warehouse_name=str(raw.get("warehouseName") or ""),
            quantity=int(raw.get("quantity") or 0),
            quantity_full=int(raw.get("quantityFull") or 0),
            in_way_to_client=int(raw.get("inWayToClient") or 0),
            in_way_from_client=int(raw.get("inWayFromClient") or 0),
        )

    @classmethod
    def _order(cls, raw: dict) -> OrderRow | None:
        article = raw.get("nmId")
        srid = str(raw.get("srid") or "")
        ordered = cls._moment(raw.get("date"))
        changed = cls._moment(raw.get("lastChangeDate"))
        # Without srid the same order cannot be recognised on the next pull, and
        # counting it twice is worse than not counting it at all.
        if not isinstance(article, int) or not srid or ordered is None or changed is None:
            return None
        return OrderRow(
            srid=srid,
            article=str(article),
            order_date=ordered.date(),
            last_change_date=changed,
            is_cancel=bool(raw.get("isCancel")),
            price=float(raw.get("finishedPrice") or raw.get("priceWithDisc") or 0),
            warehouse_type=str(raw.get("warehouseType") or ""),
        )

    @staticmethod
    def _moment(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
