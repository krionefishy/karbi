import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_penalties.domain import GROUP_PENALTIES, GROUP_STORAGE
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBRealizationClient
from backend.modules.wb_fbs_penalties.infrastructure.wb import realization as client_module
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"
PATH = "/api/v5/supplier/reportDetailByPeriod"


def report_row(rrd_id: int, **overrides) -> dict:
    row = {
        "realizationreport_id": 835082906,
        "date_from": "2026-09-01",
        "date_to": "2026-09-06",
        "create_dt": "2026-09-07",
        "rrd_id": rrd_id,
        "nm_id": 1271611253,
        "sa_name": "karbi - шуруповерт желтый",
        "subject_name": "Шуруповерты",
        "barcode": "2053497104469",
        "ts_name": "0",
        "office_name": "Склад поставщика - везу на склад WB",
        "supplier_oper_name": "Штраф",
        "order_dt": "2026-08-22T02:49:45Z",
        "sale_dt": "2026-08-31T10:28:14Z",
        "rr_dt": "2026-09-01",
        "shk_id": 57048832986,
        "bonus_type_name": "Штраф за нарушение срока передачи товара",
        "penalty": 198.06,
        "additional_payment": 0,
        "rebill_logistic_cost": 0,
        "storage_fee": 0,
        "deduction": 0,
        "acceptance": 0,
        "assembly_id": 5551052438,
        "srid": "er.i902a56689af4a0d1c940168af07cc057.0.0",
        "delivery_method": "FBS, (МГТ)",
    }
    row.update(overrides)
    return row


async def test_a_page_reports_its_cursor_and_whether_more_follows(monkeypatch) -> None:
    """Одна страница за вызов: лимит метода — запрос в час, курсор — `rrd_id` последней строки."""
    import datetime

    monkeypatch.setattr(client_module, "PAGE_LIMIT", 2)
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", PATH, body=[report_row(1), report_row(2, penalty=0, storage_fee=12.5)])
        client = WBRealizationClient(make_gateway())
        page = await client.page(SELLER, datetime.date(2026, 9, 1), datetime.date(2026, 9, 7), cursor=0)
        stub.on("GET", PATH, body=[report_row(3, penalty=0)])
        stub._rules.reverse()
        tail = await client.page(SELLER, datetime.date(2026, 9, 1), datetime.date(2026, 9, 7), cursor=page.cursor)

    assert [call["query"]["rrdid"] for call in stub.requests_to(PATH)] == [0, 2]
    assert stub.requests_to(PATH)[0]["query"] == {
        "dateFrom": "2026-09-01",
        "dateTo": "2026-09-07",
        "limit": 2,
        "rrdid": 0,
    }
    assert (page.cursor, page.exhausted) == (2, False)
    fined, stored = page.rows
    assert (fined.group, fined.amount, fined.sticker_id, fined.assembly_id) == (
        GROUP_PENALTIES,
        198.06,
        57048832986,
        5551052438,
    )
    assert (stored.group, stored.amount) == (GROUP_STORAGE, 12.5)
    assert fined.srid == "er.i902a56689af4a0d1c940168af07cc057.0.0"
    [clean] = tail.rows
    assert clean.group is None and not clean.charged and tail.exhausted


async def test_an_empty_report_is_an_exhausted_page() -> None:
    import datetime

    with respx.mock as router:
        EgressStub(router).on("GET", PATH, body=None)
        page = await WBRealizationClient(make_gateway()).page(
            SELLER, datetime.date(2026, 9, 1), datetime.date(2026, 9, 7)
        )
    assert page.rows == [] and page.exhausted and page.cursor == 0


async def test_a_non_list_answer_is_permanent() -> None:
    import datetime

    with respx.mock as router:
        EgressStub(router).on("GET", PATH, body={"error": True})
        with pytest.raises(WBPermanentError):
            await WBRealizationClient(make_gateway()).page(SELLER, datetime.date(2026, 9, 1), datetime.date(2026, 9, 7))
