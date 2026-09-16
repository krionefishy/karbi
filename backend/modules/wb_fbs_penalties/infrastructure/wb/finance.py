from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_fbs_penalties.domain import ReportHeader, ReportRow

FINANCE_BUCKET = "finance"
# Максимум строк детализации за запрос по документации WB.
PAGE_LIMIT = 100_000
LIST_LIMIT = 1000
# Основной отчёт; тип 2 — «по выкупам», он селлеру не нужен.
MAIN_REPORT = 1
# Поля детализации, которые нужны автоматизации: остальное WB не отдаёт, и ответ легче.
FIELDS = [
    "rrdId",
    "reportId",
    "dateFrom",
    "dateTo",
    "createDate",
    "srid",
    "orderId",
    "stickerId",
    "shkId",
    "orderDt",
    "saleDt",
    "rrDate",
    "nmId",
    "title",
    "vendorCode",
    "sku",
    "techSize",
    "subjectName",
    "bonusTypeName",
    "sellerOperName",
    "deliveryMethod",
    "officeName",
    "penalty",
    "deduction",
    "rebillLogisticCost",
    "paidStorage",
    "additionalPayment",
    "paidAcceptance",
]


@dataclass(frozen=True, slots=True)
class ReportPage:
    rows: list[ReportRow]
    # `rrdId` для следующей страницы; при `exhausted` дальше страниц нет.
    cursor: int
    exhausted: bool


class WBFinanceClient(WBJsonClient):
    """Отчёты реализации нового финансового API: список и детализация по ID.

    Детализация за период у токенов селлеров ограничена двумя запросами в
    сутки; по ID отчёта лимит — раз в минуту для любого токена, и это тот же
    набор строк (`rrdId` совпадает).
    """

    bucket = FINANCE_BUCKET
    api_name = "WB Finance API"
    category = "Финансы"

    async def reports(self, seller_id: str, date_from: date, date_to: date) -> list[ReportHeader]:
        """Основные суточные отчёты, у которых период пересекается с окном.

        Суточный отчёт за день D появляется на D+1 и состоит из тех же строк (те же
        `rrdId`), что потом лягут в недельный: штрафы за вчера видны сегодня, а не в
        понедельник.
        """
        collected: list[ReportHeader] = []
        offset = 0
        while True:
            payload = await self.request(
                "POST",
                "/api/finance/v1/sales-reports/list",
                seller_id,
                json={
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat(),
                    "limit": LIST_LIMIT,
                    "offset": offset,
                    "period": "daily",
                },
            )
            rows = self._rows(payload, "список отчётов")
            collected.extend(header for raw in rows if (header := self._header(raw)) is not None)
            if len(rows) < LIST_LIMIT:
                return collected
            offset += len(rows)

    async def page(self, seller_id: str, report_id: int, *, cursor: int = 0) -> ReportPage:
        """Одна страница детализации отчёта от `cursor`. Пустой ответ (204) — страниц больше нет."""
        payload = await self.request(
            "POST",
            f"/api/finance/v1/sales-reports/detailed/{report_id}",
            seller_id,
            json={"limit": PAGE_LIMIT, "rrdId": cursor, "fields": FIELDS},
        )
        raw = self._rows(payload, "детализация отчёта")
        rows = [row for item in raw if (row := self._row(item)) is not None]
        exhausted = len(raw) < PAGE_LIMIT or not rows
        return ReportPage(rows=rows, cursor=rows[-1].rrd_id if rows else cursor, exhausted=exhausted)

    def _rows(self, payload: Any, what: str) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: {what} пришёл не списком")
        return [row for row in payload if isinstance(row, dict)]

    def _header(self, raw: dict) -> ReportHeader | None:
        report_id = raw.get("reportId")
        date_from, date_to = self._day(raw.get("dateFrom")), self._day(raw.get("dateTo"))
        if not isinstance(report_id, int) or date_from is None or date_to is None:
            return None
        if int(raw.get("reportType") or MAIN_REPORT) != MAIN_REPORT:
            return None
        return ReportHeader(
            report_id=report_id,
            date_from=date_from,
            date_to=date_to,
            create_date=self._day(raw.get("createDate")),
            report_type=MAIN_REPORT,
            penalty_sum=self._money(raw.get("penaltySum")),
            deduction_sum=self._money(raw.get("deductionSum")),
        )

    def _row(self, raw: dict) -> ReportRow | None:
        rrd_id = raw.get("rrdId")
        date_from, date_to = self._day(raw.get("dateFrom")), self._day(raw.get("dateTo"))
        if not isinstance(rrd_id, int) or date_from is None or date_to is None:
            self.logger.warning("wb_report_row_unusable", extra={"row": str(raw)[:200]})
            return None
        return ReportRow(
            rrd_id=rrd_id,
            realizationreport_id=int(raw.get("reportId") or 0),
            date_from=date_from,
            date_to=date_to,
            create_dt=self._day(raw.get("createDate")),
            srid=str(raw.get("srid") or ""),
            assembly_id=self._identifier(raw.get("orderId")),
            # Стикер МП приходит строкой; `shkId` — тот же номер числом, на случай пустого стикера.
            sticker_id=self._identifier(raw.get("stickerId")) or self._identifier(raw.get("shkId")),
            order_dt=self._moment(raw.get("orderDt")),
            sale_dt=self._moment(raw.get("saleDt")),
            rr_dt=self._day(raw.get("rrDate")),
            nm_id=int(raw.get("nmId") or 0),
            sa_name=str(raw.get("vendorCode") or raw.get("title") or ""),
            subject_name=str(raw.get("subjectName") or ""),
            barcode=str(raw.get("sku") or ""),
            ts_name=str(raw.get("techSize") or ""),
            bonus_type_name=str(raw.get("bonusTypeName") or ""),
            supplier_oper_name=str(raw.get("sellerOperName") or ""),
            delivery_method=str(raw.get("deliveryMethod") or ""),
            office_name=str(raw.get("officeName") or ""),
            penalty=self._money(raw.get("penalty")),
            deduction=self._money(raw.get("deduction")),
            rebill_logistic_cost=self._money(raw.get("rebillLogisticCost")),
            storage_fee=self._money(raw.get("paidStorage")),
            additional_payment=self._money(raw.get("additionalPayment")),
            acceptance=self._money(raw.get("paidAcceptance")),
        )

    @staticmethod
    def _identifier(value: Any) -> int | None:
        """Номера приходят то числом, то строкой из цифр."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value or None
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip()) or None
        return None

    @staticmethod
    def _money(value: Any) -> float:
        """Суммы новый API отдаёт строками: '231.35'."""
        if isinstance(value, bool):
            return 0.0
        if isinstance(value, int | float):
            return round(float(value), 2)
        if isinstance(value, str):
            try:
                return round(float(value.replace(",", ".")), 2)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _day(value: Any) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _moment(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
