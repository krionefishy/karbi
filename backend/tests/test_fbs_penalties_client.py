from datetime import date

import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_penalties.domain import GROUP_PENALTIES, GROUP_STORAGE
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBFinanceClient
from backend.modules.wb_fbs_penalties.infrastructure.wb import finance as client_module
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"
LIST = "/api/finance/v1/sales-reports/list"
DETAILED = "/api/finance/v1/sales-reports/detailed/835082906"


def header(report_id: int, report_type: int = 1) -> dict:
    return {
        "reportId": report_id,
        "sellerFinanceName": "ИП Байбурин Р.Ф.",
        "dateFrom": "2026-09-01",
        "dateTo": "2026-09-06",
        "createDate": "2026-09-07",
        "currency": "RUB",
        "reportType": report_type,
        "penaltySum": "1747.52",
        "deductionSum": "974518.4",
    }


def report_row(rrd_id: int, **overrides) -> dict:
    row = {
        "additionalPayment": "0",
        "bonusTypeName": "Штраф за нарушение срока передачи товара",
        "createDate": "2026-09-07",
        "dateFrom": "2026-09-01",
        "dateTo": "2026-09-06",
        "deduction": "0",
        "deliveryMethod": "FBS, (МГТ)",
        "nmId": 1271611253,
        "officeName": "Склад поставщика - везу на склад WB",
        "orderDt": "2026-08-22T02:49:45Z",
        "orderId": 5551052438,
        "paidAcceptance": "0",
        "paidStorage": "0",
        "penalty": "198.06",
        "rebillLogisticCost": "0",
        "reportId": 835082906,
        "rrDate": "2026-09-01",
        "rrdId": rrd_id,
        "saleDt": "2026-08-31T10:28:14Z",
        "sellerOperName": "Штраф",
        "shkId": 57048832986,
        "sku": "2053497104469",
        "srid": "er.i902a56689af4a0d1c940168af07cc057.0.0",
        "stickerId": "57048832986",
        "subjectName": "Шуруповерты",
        "techSize": "0",
        "title": "Шуруповерт аккумуляторный",
        "vendorCode": "karbi - шуруповерт желтый",
    }
    row.update(overrides)
    return row


async def test_reports_keep_only_main_daily_reports() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", LIST, body=[header(835082906), header(835082909, report_type=2), {"reportId": "x"}])
        reports = await WBFinanceClient(make_gateway()).reports(SELLER, date(2026, 8, 24), date(2026, 9, 16))

    [report] = reports
    assert (report.report_id, report.date_from, report.penalty_sum, report.deduction_sum) == (
        835082906,
        date(2026, 9, 1),
        1747.52,
        974518.4,
    )
    call = stub.requests_to(LIST)[0]
    assert call["api"] == "finance"
    assert call["body"] == {
        "dateFrom": "2026-08-24",
        "dateTo": "2026-09-16",
        "limit": 1000,
        "offset": 0,
        "period": "daily",
    }


async def test_a_page_reports_its_cursor_and_whether_more_follows(monkeypatch) -> None:
    """Курсор — `rrdId` последней строки; неполная страница закрывает отчёт."""
    monkeypatch.setattr(client_module, "PAGE_LIMIT", 2)
    with respx.mock as router:
        stub = EgressStub(router)

        def reply(payload: dict) -> tuple[int, list]:
            if payload["body"]["rrdId"] == 0:
                return 200, [report_row(1), report_row(2, penalty="0", paidStorage="12.5")]
            return 200, [report_row(3, penalty="0")]

        stub.on("POST", DETAILED, reply=reply)
        client = WBFinanceClient(make_gateway())
        page = await client.page(SELLER, 835082906, cursor=0)
        tail = await client.page(SELLER, 835082906, cursor=page.cursor)

    assert [call["body"]["rrdId"] for call in stub.requests_to(DETAILED)] == [0, 2]
    assert stub.requests_to(DETAILED)[0]["body"]["limit"] == 2
    assert "stickerId" in stub.requests_to(DETAILED)[0]["body"]["fields"]
    assert (page.cursor, page.exhausted) == (2, False)
    fined, stored = page.rows
    assert (fined.group, fined.amount, fined.sticker_id, fined.assembly_id) == (
        GROUP_PENALTIES,
        198.06,
        57048832986,
        5551052438,
    )
    assert (fined.srid, fined.barcode, fined.sa_name) == (
        "er.i902a56689af4a0d1c940168af07cc057.0.0",
        "2053497104469",
        "karbi - шуруповерт желтый",
    )
    assert (stored.group, stored.amount) == (GROUP_STORAGE, 12.5)
    [clean] = tail.rows
    assert clean.group is None and not clean.charged and tail.exhausted


async def test_an_empty_answer_is_an_exhausted_page() -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", DETAILED, status=204, body=None)
        page = await WBFinanceClient(make_gateway()).page(SELLER, 835082906)
    assert page.rows == [] and page.exhausted and page.cursor == 0


async def test_a_non_list_answer_is_permanent() -> None:
    with respx.mock as router:
        EgressStub(router).on("POST", DETAILED, body={"error": True})
        with pytest.raises(WBPermanentError):
            await WBFinanceClient(make_gateway()).page(SELLER, 835082906)
