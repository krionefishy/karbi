from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.modules.wb_core.domain import SALES_REPORT_MAIN, SALES_REPORT_WEEKLY, SalesReport, SalesReportRow
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

FINANCE_BUCKET = "finance"
# WB отдаёт до ста тысяч строк за запрос, но страница целиком разбирается в памяти
# воркера, а у него 384 МБ: двадцать тысяч строк с этими полями — пик больше 400 МБ,
# пять тысяч — около 175. Больший отчёт просто читается в несколько страниц.
PAGE_LIMIT = 5_000
# Отчётов в списке за запрос — потолок WB.
LIST_LIMIT = 1000

ZERO = Decimal("0.00")
KOPECK = Decimal("0.01")
RATIO_ZERO = Decimal("0.0000")
RATIO_STEP = Decimal("0.0001")


def _money(value: Any) -> Decimal:
    """Суммы WB отдаёт строками: '231.35'. Копейки — предел точности отчёта."""
    if isinstance(value, bool) or value is None:
        return ZERO
    try:
        return Decimal(str(value).replace(",", ".")).quantize(KOPECK)
    except (InvalidOperation, ValueError):
        return ZERO


def _ratio(value: Any) -> Decimal:
    """Проценты и коэффициенты: у WB они бывают длиннее копеек, храним четыре знака."""
    if isinstance(value, bool) or value is None:
        return RATIO_ZERO
    try:
        return Decimal(str(value).replace(",", ".")).quantize(RATIO_STEP)
    except (InvalidOperation, ValueError):
        return RATIO_ZERO


def _integer(value: Any) -> int | None:
    """Номера приходят то числом, то строкой из цифр; пусто и ноль — нет номера."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value or None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip()) or None
    return None


def _count(value: Any) -> int:
    return _integer(value) or 0


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _flag(value: Any) -> bool:
    return value is True


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# Поле WB -> (поле строки зеркала, разбор). Список запрашивается у WB как есть:
# полей в ответе больше, и без `fields` страница в сто тысяч строк тяжелее вдвое.
ROW_FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "rrdId": ("rrd_id", _count),
    "reportId": ("report_id", _count),
    "giId": ("gi_id", _integer),
    "docTypeName": ("doc_type_name", _text),
    "sellerOperName": ("seller_oper_name", _text),
    "bonusTypeName": ("bonus_type_name", _text),
    "srid": ("srid", _text),
    "orderId": ("order_id", _integer),
    "shkId": ("shk_id", _integer),
    "stickerId": ("sticker_id", _integer),
    "orderUid": ("order_uid", _text),
    "trbxId": ("trbx_id", _text),
    "orderDt": ("order_dt", _moment),
    "saleDt": ("sale_dt", _moment),
    "rrDate": ("rr_date", _day),
    "fixTariffDateFrom": ("fix_tariff_date_from", _day),
    "fixTariffDateTo": ("fix_tariff_date_to", _day),
    "nmId": ("nm_id", _count),
    "vendorCode": ("vendor_code", _text),
    "title": ("title", _text),
    "brandName": ("brand_name", _text),
    "subjectName": ("subject_name", _text),
    "techSize": ("tech_size", _text),
    "sku": ("sku", _text),
    "quantity": ("quantity", _count),
    "retailPrice": ("retail_price", _money),
    "retailAmount": ("retail_amount", _money),
    "retailPriceWithDisc": ("retail_price_withdisc", _money),
    "salePercent": ("sale_percent", _ratio),
    "commissionPercent": ("commission_percent", _ratio),
    "spp": ("spp", _ratio),
    "productDiscountForReport": ("product_discount_for_report", _ratio),
    "sellerPromo": ("seller_promo", _ratio),
    "sellerPromoId": ("seller_promo_id", _integer),
    "sellerPromoDiscount": ("seller_promo_discount", _ratio),
    "kvwBase": ("kvw_base", _ratio),
    "kvw": ("kvw", _ratio),
    "supRatingUp": ("sup_rating_up", _ratio),
    "isKgvpV2": ("is_kgvp_v2", _ratio),
    "dlvPrc": ("dlv_prc", _ratio),
    "ppvzSalesCommission": ("ppvz_sales_commission", _money),
    "forPay": ("for_pay", _money),
    "ppvzReward": ("ppvz_reward", _money),
    "acquiringFee": ("acquiring_fee", _money),
    "acquiringPercent": ("acquiring_percent", _ratio),
    "acquiringBank": ("acquiring_bank", _text),
    "paymentProcessing": ("payment_processing", _text),
    "vw": ("vw", _money),
    "vwNds": ("vw_nds", _money),
    "deliveryAmount": ("delivery_amount", _count),
    "returnAmount": ("return_amount", _count),
    "deliveryService": ("delivery_service", _money),
    "rebillLogisticCost": ("rebill_logistic_cost", _money),
    "rebillLogisticOrg": ("rebill_logistic_org", _text),
    "penalty": ("penalty", _money),
    "additionalPayment": ("additional_payment", _money),
    "paidStorage": ("paid_storage", _money),
    "deduction": ("deduction", _money),
    "paidAcceptance": ("paid_acceptance", _money),
    "cashbackAmount": ("cashback_amount", _money),
    "cashbackDiscount": ("cashback_discount", _money),
    "cashbackCommissionChange": ("cashback_commission_change", _money),
    "installmentCofinancingAmount": ("installment_cofinancing_amount", _money),
    "wibesDiscountPercent": ("wibes_discount_percent", _ratio),
    "loyaltyId": ("loyalty_id", _integer),
    "loyaltyDiscount": ("loyalty_discount", _ratio),
    "warehouseLogisticsCoeff": ("warehouse_logistics_coeff", _ratio),
    "paymentSchedule": ("payment_schedule", _text),
    "officeName": ("office_name", _text),
    "ppvzOfficeName": ("ppvz_office_name", _text),
    "ppvzOfficeId": ("ppvz_office_id", _integer),
    "deliveryMethod": ("delivery_method", _text),
    "srvDbs": ("srv_dbs", _flag),
    "isB2b": ("is_b2b", _flag),
    "country": ("country", _text),
    "giBoxTypeName": ("gi_box_type_name", _text),
    "declarationNumber": ("declaration_number", _text),
}
ROW_REQUEST_FIELDS = list(ROW_FIELDS)


@dataclass(frozen=True, slots=True)
class SalesReportPage:
    rows: list[SalesReportRow]
    # `rrdId` для следующей страницы; при `exhausted` дальше страниц нет.
    cursor: int
    exhausted: bool


class WBSalesReportsClient(WBJsonClient):
    """Отчёты реализации финансового API: список за период и детализация по ID.

    Оба метода — раз в минуту на кабинет для любого токена; детализация за
    период у базового токена — дважды в сутки, поэтому строки читаются по ID
    отчёта. Строк в недельном отчёте десятки тысяч — это одна-две страницы.
    """

    bucket = FINANCE_BUCKET
    api_name = "WB Finance API"
    category = "Финансы"

    async def reports(
        self, seller_id: str, date_from: date, date_to: date, *, period: str = SALES_REPORT_WEEKLY
    ) -> list[SalesReport]:
        """Шапки отчётов обоих типов, чей период пересекается с окном."""
        collected: list[SalesReport] = []
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
                    "period": period,
                },
            )
            rows = self._rows(payload, "список отчётов")
            collected.extend(report for raw in rows if (report := self._report(raw, period)) is not None)
            if len(rows) < LIST_LIMIT:
                return collected
            offset += len(rows)

    async def page(self, seller_id: str, report_id: int, *, cursor: int = 0) -> SalesReportPage:
        """Одна страница детализации отчёта от `cursor`. Пустой ответ (204) — страниц больше нет."""
        payload = await self.request(
            "POST",
            f"/api/finance/v1/sales-reports/detailed/{report_id}",
            seller_id,
            json={"limit": PAGE_LIMIT, "rrdId": cursor, "fields": ROW_REQUEST_FIELDS},
        )
        raw = self._rows(payload, "детализация отчёта")
        rows = [row for item in raw if (row := self._row(item, report_id)) is not None]
        exhausted = len(raw) < PAGE_LIMIT or not rows
        # Курсор — наибольший номер строки, а не последний: порядок строк WB не обещает.
        newest = max((row.rrd_id for row in rows), default=cursor)
        return SalesReportPage(rows=rows, cursor=newest, exhausted=exhausted)

    def _rows(self, payload: Any, what: str) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: {what} пришёл не списком")
        return [row for row in payload if isinstance(row, dict)]

    def _report(self, raw: dict, period: str) -> SalesReport | None:
        report_id = _integer(raw.get("reportId"))
        date_from, date_to = _day(raw.get("dateFrom")), _day(raw.get("dateTo"))
        if report_id is None or date_from is None or date_to is None:
            self.logger.warning("wb_sales_report_unusable", extra={"row": str(raw)[:200]})
            return None
        return SalesReport(
            report_id=report_id,
            report_type=_integer(raw.get("reportType")) or SALES_REPORT_MAIN,
            period=period,
            date_from=date_from,
            date_to=date_to,
            create_date=_day(raw.get("createDate")),
            currency=_text(raw.get("currency")),
            seller_finance_name=_text(raw.get("sellerFinanceName")),
            retail_amount_sum=_money(raw.get("retailAmountSum")),
            for_pay_sum=_money(raw.get("forPaySum")),
            delivery_service_sum=_money(raw.get("deliveryServiceSum")),
            paid_storage_sum=_money(raw.get("paidStorageSum")),
            paid_acceptance_sum=_money(raw.get("paidAcceptanceSum")),
            deduction_sum=_money(raw.get("deductionSum")),
            penalty_sum=_money(raw.get("penaltySum")),
            additional_payment_sum=_money(raw.get("additionalPaymentSum")),
            cashback_amount_sum=_money(raw.get("cashbackAmountSum")),
            cashback_discount_sum=_money(raw.get("cashbackDiscountSum")),
            cashback_commission_change_sum=_money(raw.get("cashbackCommissionChangeSum")),
            bank_payment_sum=_money(raw.get("bankPaymentSum")),
        )

    def _row(self, raw: dict, report_id: int) -> SalesReportRow | None:
        if _integer(raw.get("rrdId")) is None:
            self.logger.warning("wb_sales_report_row_unusable", extra={"row": str(raw)[:200]})
            return None
        values = {name: parse(raw.get(key)) for key, (name, parse) in ROW_FIELDS.items()}
        # Отчёт спрашивали по номеру: строка без него (или с чужим) всё равно его.
        values["report_id"] = report_id
        return SalesReportRow(**values)
