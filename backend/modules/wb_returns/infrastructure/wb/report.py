"""Отчёт «Возвраты и перемещения» из аналитического API WB.

Один метод, окно не больше 31 дня, лимит — запрос в минуту на кабинет (его
держит шлюз). Отчёт отдаёт всё за окно целиком, без страниц: отсюда окно в две
недели вместо месяца при обычном проходе.
"""

from datetime import date
from typing import Any

from backend.modules.wb_core.infrastructure.wb import ANALYTICS_BUCKET, WBJsonClient, WBPermanentError
from backend.modules.wb_returns.domain import ReturnItem
from backend.modules.wb_returns.infrastructure.wb.parsing import as_date, as_int, as_moment, as_text

REPORT_PATH = "/api/v1/analytics/goods-return"
# По документации WB — максимум за один запрос.
MAX_WINDOW_DAYS = 31


class WBReturnsReportClient(WBJsonClient):
    bucket = ANALYTICS_BUCKET
    api_name = "WB Analytics API"
    category = "Аналитика"

    async def report(self, seller_id: str, date_from: date, date_to: date) -> list[ReturnItem]:
        payload = await self.request(
            "GET",
            REPORT_PATH,
            seller_id,
            params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
        )
        rows = payload.get("report") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: отчёт о возвратах пришёл без поля report")
        return [item for item in (self._item(row) for row in rows if isinstance(row, dict)) if item is not None]

    @staticmethod
    def _item(row: dict[str, Any]) -> ReturnItem | None:
        shk_id = as_int(row.get("shkId"))
        if not shk_id:
            # Без стикера строку не к чему привязать: повторный сбор задвоил бы её.
            return None
        return ReturnItem(
            shk_id=shk_id,
            sticker_id=as_text(row.get("stickerId")),
            srid=as_text(row.get("srid")),
            order_id=as_int(row.get("orderId")),
            nm_id=as_int(row.get("nmId")),
            barcode=as_text(row.get("barcode")),
            brand=as_text(row.get("brand")),
            subject_name=as_text(row.get("subjectName")),
            tech_size=as_text(row.get("techSize")),
            return_type=as_text(row.get("returnType")),
            reason=as_text(row.get("reason")),
            status=as_text(row.get("status")),
            is_active=bool(as_int(row.get("isStatusActive"))),
            dst_office_id=as_int(row.get("dstOfficeId")) or None,
            dst_office_address=as_text(row.get("dstOfficeAddress")),
            order_dt=as_date(row.get("orderDt")),
            ready_to_return_dt=as_moment(row.get("readyToReturnDt")),
            expired_dt=as_moment(row.get("expiredDt")),
            completed_dt=as_moment(row.get("completedDt")),
        )
