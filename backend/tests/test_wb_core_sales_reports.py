"""Зеркало отчётов реализации: клиент финансового API и сбор в wb_core."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete

from backend.modules.wb_core.application import MirrorService, SalesReportMirror
from backend.modules.wb_core.domain import (
    MIRROR_SALES_REPORTS,
    SALES_REPORT_BUYOUT,
    SALES_REPORT_MAIN,
    SalesReport,
    SalesReportRow,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    ROW_REQUEST_FIELDS,
    SalesReportPage,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBPermanentError,
    WBSalesReportsClient,
    WBTemporaryError,
)
from backend.modules.wb_core.infrastructure.wb import finance as client_module
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway
from backend.workers.wb_core.worker import WBCoreWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SELLER = "seller-1"
LIST = "/api/finance/v1/sales-reports/list"
DETAILED = "/api/finance/v1/sales-reports/detailed/855649022"
MAIN, BUYOUT = 855649022, 855649019


def header(report_id: int, report_type: int = SALES_REPORT_MAIN, **overrides) -> dict:
    row = {
        "reportId": report_id,
        "sellerFinanceName": "ИП Байбурин Р. Ф.",
        "dateFrom": "2026-09-21",
        "dateTo": "2026-09-27",
        "createDate": "2026-09-28",
        "currency": "RUB",
        "reportType": report_type,
        "retailAmountSum": "5121866.93",
        "forPaySum": "5275247.39",
        "avgSalePercent": 0,
        "deliveryServiceSum": "316408.59",
        "paidStorageSum": "291.1",
        "paidAcceptanceSum": "480",
        "deductionSum": "916405.84",
        "penaltySum": "9676.94",
        "additionalPaymentSum": "0",
        "cashbackAmountSum": "198540",
        "cashbackDiscountSum": "1889.93",
        "cashbackCommissionChangeSum": "10666.96",
        "paymentSchedule": "0",
        "bankPaymentSum": "3822777.96",
    }
    row.update(overrides)
    return row


def report_row(rrd_id: int, **overrides) -> dict:
    row = {
        "reportId": MAIN,
        "dateFrom": "2026-09-21",
        "dateTo": "2026-09-27",
        "createDate": "2026-09-28",
        "currency": "RUB",
        "reportType": 1,
        "rrdId": rrd_id,
        "giId": 123456,
        "dlvPrc": 1.8,
        "subjectName": "Шуруповерты",
        "nmId": 1112466805,
        "brandName": "KARBI",
        "vendorCode": "KARBI - Бесщеточный шуруповерт ударный",
        "title": "Шуруповерт аккумуляторный",
        "techSize": "0",
        "sku": "2051917005518",
        "docTypeName": "Продажа",
        "quantity": 1,
        "retailPrice": "11178",
        "retailAmount": "3589.29",
        "salePercent": 45,
        "commissionPercent": 24.15,
        "officeName": "Склад WB",
        "sellerOperName": "Продажа",
        "orderDt": "2026-09-14T00:00:00Z",
        "saleDt": "2026-09-21T10:28:14Z",
        "rrDate": "2026-09-21",
        "shkId": 57048832986,
        "retailPriceWithDisc": "6141.2",
        "deliveryAmount": 0,
        "returnAmount": 0,
        "deliveryService": "0",
        "giBoxTypeName": "Монопаллета",
        "productDiscountForReport": 45,
        "sellerPromo": 0,
        "spp": 41.55,
        "kvwBase": 24.15,
        "kvw": 1.81,
        "supRatingUp": 0,
        "isKgvpV2": 0,
        "ppvzSalesCommission": "23.74",
        "forPay": "3576.4",
        "ppvzReward": "0",
        "acquiringFee": "140.58",
        "acquiringPercent": 2.29,
        "paymentProcessing": "Комиссия за организацию платежа с НДС",
        "acquiringBank": "Вайлдберриз Банк",
        "vw": "22.25",
        "vwNds": "4.45",
        "ppvzOfficeName": "Москва Очаковское шоссе 6к2",
        "ppvzOfficeId": 105383,
        "bonusTypeName": "",
        "stickerId": "57048832986",
        "country": "Россия",
        "srvDbs": False,
        "penalty": "0",
        "additionalPayment": "0",
        "rebillLogisticCost": "0",
        "paidStorage": "0",
        "deduction": "0",
        "paidAcceptance": "0",
        "orderId": 5551052438,
        "isB2b": False,
        "trbxId": "",
        "cashbackAmount": "0",
        "cashbackDiscount": "0",
        "cashbackCommissionChange": "0",
        "paymentSchedule": "0",
        "deliveryMethod": "FBW",
        "srid": "er.i902a56689af4a0d1c940168af07cc057.0.0",
        "kiz": "0102900000376311210G2CIS",
        "b2bCustomerTin": "",
    }
    row.update(overrides)
    return row


async def test_the_list_keeps_both_report_types_with_money_to_the_kopeck() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", LIST, body=[header(MAIN), header(BUYOUT, SALES_REPORT_BUYOUT), {"reportId": "x"}])
        reports = await WBSalesReportsClient(make_gateway()).reports(SELLER, date(2026, 1, 1), date(2026, 9, 30))

    assert [(report.report_id, report.report_type) for report in reports] == [(MAIN, 1), (BUYOUT, 2)]
    main = reports[0]
    assert (main.date_from, main.date_to, main.create_date) == (date(2026, 9, 21), date(2026, 9, 27), date(2026, 9, 28))
    assert (main.retail_amount_sum, main.for_pay_sum) == (Decimal("5121866.93"), Decimal("5275247.39"))
    assert (main.deduction_sum, main.cashback_commission_change_sum) == (Decimal("916405.84"), Decimal("10666.96"))
    assert main.period == "weekly"
    call = stub.requests_to(LIST)[0]
    assert call["api"] == "finance"
    assert call["body"] == {
        "dateFrom": "2026-01-01",
        "dateTo": "2026-09-30",
        "limit": 1000,
        "offset": 0,
        "period": "weekly",
    }


async def test_a_page_asks_only_for_the_fields_the_mirror_stores() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", DETAILED, body=[report_row(11), report_row(12, deliveryAmount=1, deliveryService="63.5")])
        page = await WBSalesReportsClient(make_gateway()).page(SELLER, MAIN, cursor=0)

    assert (page.cursor, page.exhausted, len(page.rows)) == (12, True, 2)
    sale, delivery = page.rows
    assert (sale.rrd_id, sale.report_id, sale.nm_id, sale.srid) == (11, MAIN, 1112466805, report_row(11)["srid"])
    assert (sale.retail_amount, sale.for_pay, sale.retail_price_withdisc) == (
        Decimal("3589.29"),
        Decimal("3576.40"),
        Decimal("6141.20"),
    )
    assert (sale.commission_percent, sale.spp, sale.quantity) == (Decimal("24.15"), Decimal("41.55"), 1)
    assert sale.sale_dt == datetime(2026, 9, 21, 10, 28, 14, tzinfo=UTC) and sale.rr_date == date(2026, 9, 21)
    assert (sale.sticker_id, sale.order_id, sale.ppvz_office_id) == (57048832986, 5551052438, 105383)
    assert (sale.srv_dbs, sale.is_b2b, sale.doc_type_name) == (False, False, "Продажа")
    assert (delivery.delivery_amount, delivery.delivery_service) == (1, Decimal("63.50"))
    call = stub.requests_to(DETAILED)[0]
    assert call["body"] == {"limit": client_module.PAGE_LIMIT, "rrdId": 0, "fields": ROW_REQUEST_FIELDS}
    # Коды маркировки и реквизиты покупателей в зеркало не просятся.
    assert "kiz" not in ROW_REQUEST_FIELDS and "b2bCustomerTin" not in ROW_REQUEST_FIELDS


async def test_a_full_page_is_not_the_last_one_and_a_row_without_rrdid_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "PAGE_LIMIT", 2)
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", DETAILED, body=[report_row(11), {"nmId": 1}, report_row(13)])
        page = await WBSalesReportsClient(make_gateway()).page(SELLER, MAIN, cursor=0)

    assert (page.cursor, page.exhausted, [row.rrd_id for row in page.rows]) == (13, False, [11, 13])


async def test_a_non_list_answer_is_a_permanent_error() -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", LIST, body={"error": "nope"})
        with pytest.raises(WBPermanentError):
            await WBSalesReportsClient(make_gateway()).reports(SELLER, date(2026, 9, 1), date(2026, 9, 30))


# --- сбор в зеркало -------------------------------------------------------------------


def money(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def sales_report(report_id: int, report_type: int = SALES_REPORT_MAIN, **overrides: Any) -> SalesReport:
    report = SalesReport(
        report_id=report_id,
        report_type=report_type,
        period="weekly",
        date_from=date(2026, 9, 21),
        date_to=date(2026, 9, 27),
        create_date=date(2026, 9, 28),
        currency="RUB",
        seller_finance_name="ИП Байбурин Р. Ф.",
        retail_amount_sum=money("7000"),
        for_pay_sum=money("6000"),
        delivery_service_sum=money("63.5"),
        paid_storage_sum=money("0"),
        paid_acceptance_sum=money("0"),
        deduction_sum=money("0"),
        penalty_sum=money("0"),
        additional_payment_sum=money("0"),
        cashback_amount_sum=money("0"),
        cashback_discount_sum=money("0"),
        cashback_commission_change_sum=money("0"),
        bank_payment_sum=money("5000"),
    )
    return replace(report, **overrides)


def row(rrd_id: int, report_id: int = MAIN, **overrides: Any) -> SalesReportRow:
    parsed = WBSalesReportsClient(make_gateway())._row(report_row(rrd_id, reportId=report_id), report_id)
    assert parsed is not None
    return replace(parsed, **overrides)


class FakeSalesReports(WBSalesReportsClient):
    """Список отчётов и их строки страницами по две; падает на заданной странице."""

    def __init__(
        self,
        reports: list[SalesReport],
        rows: dict[int, list[SalesReportRow]],
        *,
        failing_page: int | None = None,
    ) -> None:
        super().__init__(make_gateway())
        self.headers = reports
        self.rows = rows
        self.failing_page = failing_page
        self.refused: set[int] = set()
        self.stuck: set[int] = set()
        self.windows: list[tuple[date, date]] = []
        self.page_calls: list[tuple[int, int]] = []

    async def reports(self, seller_id: str, date_from: date, date_to: date, *, period: str = "weekly"):
        self.windows.append((date_from, date_to))
        return list(self.headers)

    async def page(self, seller_id: str, report_id: int, *, cursor: int = 0) -> SalesReportPage:
        if self.failing_page is not None and len(self.page_calls) >= self.failing_page:
            raise WBTemporaryError("WB Finance API отвечает HTTP 429")
        if report_id in self.refused:
            self.page_calls.append((report_id, cursor))
            raise WBPermanentError("WB Finance API отклонил запрос: HTTP 400")
        if report_id in self.stuck:
            self.page_calls.append((report_id, cursor))
            return SalesReportPage(rows=self.rows[report_id][:2], cursor=cursor, exhausted=False)
        self.page_calls.append((report_id, cursor))
        newer = [item for item in self.rows.get(report_id, []) if item.rrd_id > cursor]
        page = newer[:2]
        return SalesReportPage(rows=page, cursor=page[-1].rrd_id if page else cursor, exhausted=len(page) < 2)


def mirror(database: Database, client: WBSalesReportsClient, *, per_run: int = 4) -> MirrorService:
    gateway = make_gateway()
    return MirrorService(
        database,
        content=WBContentClient(gateway),
        analytics=WBAnalyticsClient(gateway),
        marketplace=WBMarketplaceClient(gateway),
        feedbacks=WBFeedbackClient(gateway),
        chats=WBChatClient(gateway),
        sales_reports=client,
        sales_reports_history_from=date(2026, 1, 1),
        sales_reports_per_run=per_run,
    )


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield database
    finally:
        await database.disconnect()


@pytest_asyncio.fixture
async def seller(database: Database) -> AsyncIterator[uuid.UUID]:
    async with database.session() as session:
        model = SellerModel(name="ИП Отчёты", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


def at(hour: int, minute: int = 0, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


async def test_reports_are_listed_from_the_history_start_and_rows_read_to_the_end(
    database: Database, seller: uuid.UUID
) -> None:
    rows = {MAIN: [row(11), row(12), row(13, retail_amount=money("-178.71"))], BUYOUT: [row(21, BUYOUT)]}
    client = FakeSalesReports([sales_report(MAIN), sales_report(BUYOUT, SALES_REPORT_BUYOUT)], rows)

    outcome = await mirror(database, client).collect_sales_reports(seller, now=at(6))

    assert (outcome.reports, outcome.loaded, outcome.pages, outcome.rows, outcome.pending) == (2, 2, 3, 4, 0)
    # Окно списка — от начала истории до сегодняшнего дня по Москве.
    assert client.windows == [(date(2026, 1, 1), date(2026, 9, 29))]
    assert client.page_calls == [(MAIN, 0), (MAIN, 12), (BUYOUT, 0)]
    async with database.session() as session:
        port = SalesReportMirror(session)
        reports = await port.reports(seller, since=date(2026, 9, 27), until=date(2026, 9, 27))
        stored = await port.rows(seller, [MAIN])
        state = await port.state(seller)
    assert [(item.report.report_id, item.loaded) for item in reports] == [(MAIN, True), (BUYOUT, True)]
    assert reports[0].report.for_pay_sum == money("6000")
    assert [item.rrd_id for item in stored] == [11, 12, 13]
    assert stored[0].for_pay == money("3576.4") and stored[2].retail_amount == money("-178.71")
    assert state is not None and state.collected_at == at(6) and state.error is None


async def test_a_failure_keeps_the_pages_written_and_the_next_run_resumes_from_the_cursor(
    database: Database, seller: uuid.UUID
) -> None:
    rows = {MAIN: [row(11), row(12), row(13), row(14), row(15)]}
    client = FakeSalesReports([sales_report(MAIN)], rows, failing_page=2)

    with pytest.raises(WBTemporaryError):
        await mirror(database, client).collect_sales_reports(seller, now=at(6))

    async with database.session() as session:
        repository = MirrorRepository(session)
        state = await repository.state(seller, MIRROR_SALES_REPORTS)
        pending = await repository.pending_sales_reports(seller, limit=10)
        stored = await repository.sales_report_rows(seller, [MAIN])
    assert state is not None and state.collected_at is None and state.error
    assert pending == [(MAIN, 14)] and [item.rrd_id for item in stored] == [11, 12, 13, 14]

    client.failing_page = None
    outcome = await mirror(database, client).collect_sales_reports(seller, now=at(7))

    assert (outcome.loaded, outcome.pages, outcome.rows) == (1, 1, 1)
    assert client.page_calls[-1] == (MAIN, 14)
    async with database.session() as session:
        [report] = await SalesReportMirror(session).reports(seller, since=date(2026, 9, 21), until=date(2026, 9, 21))
        assert report.rows_loaded_at == at(7)
        assert [item.rrd_id for item in await SalesReportMirror(session).rows(seller, [MAIN])] == [11, 12, 13, 14, 15]


async def test_only_so_many_reports_are_read_per_run_and_loaded_ones_are_not_reread(
    database: Database, seller: uuid.UUID
) -> None:
    older = sales_report(700, date_from=date(2026, 9, 14), date_to=date(2026, 9, 20))
    client = FakeSalesReports([sales_report(MAIN), older], {MAIN: [row(11)], 700: [row(31, 700)]})
    service = mirror(database, client, per_run=1)

    first = await service.collect_sales_reports(seller, now=at(6))
    assert (first.loaded, first.pending) == (1, 0)
    # Старший отчёт первым: история дочитывается с начала.
    assert client.page_calls == [(700, 0)]

    second = await service.collect_sales_reports(seller, now=at(7))
    third = await service.collect_sales_reports(seller, now=at(8))
    assert (second.loaded, third.loaded, third.pages) == (1, 0, 0)
    assert client.page_calls == [(700, 0), (MAIN, 0)]
    async with database.session() as session:
        reports = await SalesReportMirror(session).reports(seller, since=date(2026, 9, 1), until=date(2026, 9, 30))
    assert [(item.report.report_id, item.loaded) for item in reports] == [(700, True), (MAIN, True)]


async def test_a_loaded_report_is_checked_against_wb_totals_with_returns_subtracted(
    database: Database, seller: uuid.UUID, caplog
) -> None:
    """Цифры живого отчёта: три продажи и возврат, WB в шапке возврат вычитает."""
    rows = [
        row(11, retail_amount=money("2561"), for_pay=money("2078.22"), delivery_service=money("423.44")),
        row(12, retail_amount=money("2070.07"), for_pay=money("1711.43"), delivery_service=money("37.07")),
        row(13, retail_amount=money("1435.59"), for_pay=money("1101.13"), delivery_service=money("209.7")),
        row(14, doc_type_name="Возврат", retail_amount=money("2447.06"), for_pay=money("2150.23")),
    ]
    totals: dict[str, Any] = {
        "retail_amount_sum": money("3619.6"),
        "for_pay_sum": money("2740.55"),
        "delivery_service_sum": money("670.21"),
    }
    matching = FakeSalesReports([sales_report(MAIN, **totals)], {MAIN: rows})
    wrong = FakeSalesReports([sales_report(700, **{**totals, "for_pay_sum": money("1")})], {700: [row(31, 700)]})

    with caplog.at_level("WARNING", logger="wb.core.mirror"):
        await mirror(database, matching).collect_sales_reports(seller, now=at(6))
        assert not [item for item in caplog.records if item.getMessage() == "sales_report_sums_mismatch"]
        outcome = await mirror(database, wrong).collect_sales_reports(seller, now=at(7))

    # Расхождение — строка в журнале, а отчёт остаётся дочитанным: других строк WB не отдаст.
    assert outcome.loaded == 1
    [record] = [item for item in caplog.records if item.getMessage() == "sales_report_sums_mismatch"]
    assert record.report_id == 700 and record.mismatch["for_pay_sum"] == ("1.00", "3576.40")


async def test_sales_reports_are_due_by_interval_and_do_not_wait_for_the_catalog(
    database: Database, seller: uuid.UUID
) -> None:
    service = mirror(database, FakeSalesReports([], {}))
    interval = timedelta(minutes=SETTINGS.core_mirror.sales_reports_interval_minutes)
    schedule = WBCoreWorker(database, service, SETTINGS, now=lambda: at(10))

    assert schedule.due_since(MIRROR_SALES_REPORTS, at(10)) == at(10) - interval
    assert await schedule.collect_due(MIRROR_SALES_REPORTS, at(10)) == 1
    assert (
        await WBCoreWorker(database, service, SETTINGS, now=lambda: at(10, 5)).collect_due(
            MIRROR_SALES_REPORTS, at(10, 5)
        )
        == 0
    )
    later = at(10) + interval + timedelta(minutes=1)
    assert (
        await WBCoreWorker(database, service, SETTINGS, now=lambda: later).collect_due(MIRROR_SALES_REPORTS, later) == 1
    )


async def test_a_page_of_thousands_of_rows_fits_the_database_parameter_limit(
    database: Database, seller: uuid.UUID
) -> None:
    """У asyncpg не больше 32 767 параметров на запрос, а в строке отчёта их под восемьдесят."""
    rows = [row(index) for index in range(1, 1201)]
    async with database.session() as session:
        repository = MirrorRepository(session)
        await repository.upsert_sales_reports(seller, [sales_report(MAIN)], now=at(6))
        assert await repository.insert_sales_report_rows(seller, rows, now=at(6)) == 1200
        await session.commit()
        [totals] = await repository.sales_report_totals(seller, [MAIN])
    assert (totals.rows, totals.quantity, totals.month) == (1200, 1200, date(2026, 9, 1))


async def test_a_report_wb_refuses_does_not_lock_out_the_newer_ones(database: Database, seller: uuid.UUID) -> None:
    """Отказавший отчёт — старший в очереди: без пропуска новые недели не читались бы никогда."""
    older = sales_report(700, date_from=date(2026, 9, 14), date_to=date(2026, 9, 20))
    looping = sales_report(600, date_from=date(2026, 9, 7), date_to=date(2026, 9, 13))
    client = FakeSalesReports(
        [sales_report(MAIN), older, looping], {MAIN: [row(11)], 700: [row(31, 700)], 600: [row(41, 600), row(42, 600)]}
    )
    client.refused, client.stuck = {700}, {600}

    with pytest.raises(WBPermanentError) as error:
        await mirror(database, client).collect_sales_reports(seller, now=at(6))

    # Курсор, который не двигается, — отказ, а не вечный цикл; оба отказа названы.
    assert "отчёт 700" in str(error.value) and "курсор отчёта 600 не двигается" in str(error.value)
    assert client.page_calls == [(600, 0), (700, 0), (MAIN, 0)]
    async with database.session() as session:
        reports = await SalesReportMirror(session).reports(seller, since=date(2026, 9, 1), until=date(2026, 9, 30))
        state = await SalesReportMirror(session).state(seller)
    assert [(item.report.report_id, item.loaded) for item in reports] == [(600, False), (700, False), (MAIN, True)]
    assert state is not None and state.collected_at is None and "отчёт 700" in (state.error or "")
