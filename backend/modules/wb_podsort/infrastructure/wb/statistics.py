from datetime import date, datetime
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_podsort.domain import OrderLine, order_region

STATISTICS_BUCKET = "statistics"
# Так WB помечает заказ со склада продавца (FBS/DBS); FBO — «Склад WB».
SELLER_WAREHOUSE = "Склад продавца"


class WBPodsortStatisticsClient(WBJsonClient):
    """Заказы за сутки из статистики WB — по баркоду и региону покупателя.

    Спрашиваем по дню заказа (`flag=1`): один запрос — все заказы суток, как бы
    они ни менялись потом, и повтор даёт тот же день. Три месяца одним ответом
    не получить: у крупного кабинета это сотня тысяч строк, больше потолка
    ответа шлюза, а у `flag=1` нет курсора. Отменённые заказы считаются — как в
    «Итого заказов» отчёта «География заказов», с которым сверяется подсорт.
    """

    bucket = STATISTICS_BUCKET
    api_name = "WB Statistics API"
    category = "Статистика"
    path = "/api/v1/supplier/orders"

    async def orders_on(self, seller_id: str, day: date) -> list[OrderLine]:
        payload = await self.request("GET", self.path, seller_id, params={"dateFrom": day.isoformat(), "flag": 1})
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name} вернул не список")
        lines: list[OrderLine] = []
        seen: set[str] = set()
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            # WB решает, что значит флаг: чужой день в ответе раздул бы наш.
            if self._day(raw.get("date")) != day:
                continue
            srid = str(raw.get("srid") or "")
            if srid:
                if srid in seen:
                    continue
                seen.add(srid)
            line = self._line(raw)
            if line is not None:
                lines.append(line)
        return lines

    def _line(self, raw: dict[str, Any]) -> OrderLine | None:
        barcode = str(raw.get("barcode") or "")
        nm_id = raw.get("nmId")
        if not barcode or not isinstance(nm_id, int):
            self.logger.warning("wb_podsort_order_without_barcode", extra={"srid": str(raw.get("srid") or "")})
            return None
        return OrderLine(
            barcode=barcode,
            nm_id=nm_id,
            vendor_code=str(raw.get("supplierArticle") or ""),
            subject=str(raw.get("subject") or ""),
            tech_size=str(raw.get("techSize") or ""),
            region=order_region(str(raw.get("countryName") or ""), str(raw.get("oblastOkrugName") or "")),
            fbs=str(raw.get("warehouseType") or "") == SELLER_WAREHOUSE,
        )

    @staticmethod
    def _day(value: Any) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
