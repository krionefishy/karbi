from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_fbs_penalties.domain import ReportRow

STATISTICS_BUCKET = "statistics"
# Максимум строк за запрос по документации WB. Лимит метода — 1 запрос в
# минуту, поэтому страницы берутся самыми крупными.
PAGE_LIMIT = 100_000


@dataclass(frozen=True, slots=True)
class ReportPage:
    rows: list[ReportRow]
    # `rrdid` для следующей страницы; при `exhausted` дальше страниц нет.
    cursor: int
    exhausted: bool


class WBRealizationClient(WBJsonClient):
    """Детализация отчёта реализации: те же строки, что селлер скачивает из кабинета в Excel."""

    bucket = STATISTICS_BUCKET
    api_name = "WB Statistics API"
    category = "Статистика"
    path = "/api/v5/supplier/reportDetailByPeriod"

    async def page(self, seller_id: str, date_from: date, date_to: date, *, cursor: int = 0) -> ReportPage:
        """Одна страница детализации от `cursor` (`rrdid` последней строки прошлой страницы).

        Один запрос за вызов нарочно: лимит метода у токенов селлеров — запрос в
        час, и второй страницей за тот же проход WB ответил бы 429.
        """
        payload = await self.request(
            "GET",
            self.path,
            seller_id,
            params={
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "limit": PAGE_LIMIT,
                "rrdid": cursor,
            },
        )
        raw = self._rows(payload)
        rows = [row for item in raw if (row := self._row(item)) is not None]
        exhausted = len(raw) < PAGE_LIMIT or not rows
        return ReportPage(rows=rows, cursor=rows[-1].rrd_id if rows else cursor, exhausted=exhausted)

    def _rows(self, payload: Any) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: детализация отчёта пришла не списком")
        return [row for row in payload if isinstance(row, dict)]

    def _row(self, raw: dict) -> ReportRow | None:
        rrd_id = raw.get("rrd_id")
        date_from, date_to = self._day(raw.get("date_from")), self._day(raw.get("date_to"))
        if not isinstance(rrd_id, int) or date_from is None or date_to is None:
            self.logger.warning("wb_report_row_unusable", extra={"row": str(raw)[:200]})
            return None
        assembly = raw.get("assembly_id")
        sticker = raw.get("shk_id")
        return ReportRow(
            rrd_id=rrd_id,
            realizationreport_id=int(raw.get("realizationreport_id") or 0),
            date_from=date_from,
            date_to=date_to,
            create_dt=self._day(raw.get("create_dt")),
            srid=str(raw.get("srid") or ""),
            assembly_id=int(assembly) if isinstance(assembly, int) and assembly else None,
            sticker_id=int(sticker) if isinstance(sticker, int) and sticker else None,
            order_dt=self._moment(raw.get("order_dt")),
            sale_dt=self._moment(raw.get("sale_dt")),
            rr_dt=self._day(raw.get("rr_dt")),
            nm_id=int(raw.get("nm_id") or 0),
            sa_name=str(raw.get("sa_name") or ""),
            subject_name=str(raw.get("subject_name") or ""),
            barcode=str(raw.get("barcode") or ""),
            ts_name=str(raw.get("ts_name") or ""),
            bonus_type_name=str(raw.get("bonus_type_name") or ""),
            supplier_oper_name=str(raw.get("supplier_oper_name") or ""),
            delivery_method=str(raw.get("delivery_method") or ""),
            office_name=str(raw.get("office_name") or ""),
            penalty=self._money(raw.get("penalty")),
            deduction=self._money(raw.get("deduction")),
            rebill_logistic_cost=self._money(raw.get("rebill_logistic_cost")),
            storage_fee=self._money(raw.get("storage_fee")),
            additional_payment=self._money(raw.get("additional_payment")),
            acceptance=self._money(raw.get("acceptance")),
        )

    @staticmethod
    def _money(value: Any) -> float:
        return round(float(value), 2) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0

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
