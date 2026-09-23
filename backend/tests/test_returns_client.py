from datetime import UTC, date, datetime

import pytest
import respx

from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_returns.domain import RETURN_READY, RETURN_TRANSIT, RETURN_UNKNOWN
from backend.modules.wb_returns.infrastructure.wb import WBClaimsClient, WBReturnsReportClient
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"
REPORT = "/api/v1/analytics/goods-return"
CLAIMS = "/api/v1/claims"


def report_row(shk_id: int, **overrides) -> dict:
    row = {
        "barcode": "2053497104469",
        "brand": "KARBI",
        "completedDt": None,
        "dstOfficeAddress": "посёлок Развилка Посёлок Развилка 52к1",
        "dstOfficeId": 50041474,
        "expiredDt": None,
        "isStatusActive": 1,
        "nmId": 1271611253,
        "orderDt": "2026-09-14",
        "orderId": 0,
        "readyToReturnDt": "2026-09-17T11:19:17",
        "reason": "",
        "returnType": "Возврат брака",
        "shkId": shk_id,
        "srid": "71cf3c3431a64596b8a581cb1f1403f8",
        "status": "Готов к выдаче",
        "stickerId": str(shk_id),
        "subjectName": "Шуруповерты",
        "techSize": "0",
    }
    row.update(overrides)
    return row


def claim_row(claim_id: str, **overrides) -> dict:
    row = {
        "id": claim_id,
        "claim_type": 1,
        "status": 0,
        "status_ex": 0,
        "nm_id": 1152689729,
        "user_comment": "заметил дома, товар не как в описании",
        "wb_comment": None,
        "dt": "2026-09-21T17:56:24.073185",
        "imt_name": "Бинокль профессиональный туристический",
        "order_dt": "2026-09-16T09:31:01",
        "dt_update": "2026-09-21T17:56:24.073185",
        "photos": ["//claim-basket-01.wbbasket.ru/x/1.webp"],
        "video_paths": [],
        "actions": ["autorefund1", "approve2", "reject1"],
        "price": 2320,
        "currency_code": "643",
        "srid": "eAD.r49d8ca90a38c4bd18f2e09cecf06b913.0.0",
        "origin_id_info": None,
        "delivery_dt": "2026-09-20T11:27:42",
    }
    row.update(overrides)
    return row


@respx.mock
async def test_report_parses_rows_and_moscow_dates() -> None:
    stub = EgressStub(respx.mock)
    stub.on(
        "GET",
        REPORT,
        body={
            "report": [
                report_row(1),
                report_row(2, status="В пути в пвз", readyToReturnDt=None),
                report_row(3, status="Что-то новое", isStatusActive=0),
                {"nmId": 5, "status": "Готов к выдаче"},
            ]
        },
    )
    client = WBReturnsReportClient(make_gateway())

    items = await client.report(SELLER, date(2026, 9, 1), date(2026, 9, 22))

    assert [item.shk_id for item in items] == [1, 2, 3]
    assert items[0].status_key == RETURN_READY
    assert items[1].status_key == RETURN_TRANSIT
    assert items[2].status_key == RETURN_UNKNOWN and not items[2].is_active
    # Дата без зоны — московская: 11:19 МСК = 08:19 UTC.
    assert items[0].ready_to_return_dt == datetime(2026, 9, 17, 8, 19, 17, tzinfo=UTC)
    assert items[0].order_dt == date(2026, 9, 14)
    assert items[0].title == "Шуруповерты · KARBI"
    call = stub.requests_to(REPORT)[0]
    assert call["api"] == "analytics"
    assert call["query"] == {"dateFrom": "2026-09-01", "dateTo": "2026-09-22"}


@respx.mock
async def test_report_without_report_field_is_permanent_error() -> None:
    stub = EgressStub(respx.mock)
    stub.on("GET", REPORT, body={"data": []})
    client = WBReturnsReportClient(make_gateway())

    with pytest.raises(WBPermanentError):
        await client.report(SELLER, date(2026, 9, 1), date(2026, 9, 22))


@respx.mock
async def test_claims_paginate_until_total() -> None:
    stub = EgressStub(respx.mock)
    first = [claim_row(f"00000000-0000-0000-0000-{index:012d}") for index in range(200)]
    second = [claim_row("11111111-1111-1111-1111-111111111111"), {"id": "not-a-uuid", "dt": "2026-09-21T10:00:00"}]

    def reply(payload: dict) -> tuple[int, dict]:
        offset = int(payload["query"]["offset"])
        return 200, {"claims": first if offset == 0 else second, "total": 201}

    stub.on("GET", CLAIMS, reply=reply)
    client = WBClaimsClient(make_gateway())

    claims = await client.claims(SELLER, archive=False)

    assert len(claims) == 201
    assert claims[0].is_open and not claims[0].is_archive
    assert claims[0].photo_urls == ("https://claim-basket-01.wbbasket.ru/x/1.webp",)
    assert claims[0].dt == datetime(2026, 9, 21, 14, 56, 24, 73185, tzinfo=UTC)
    calls = stub.requests_to(CLAIMS)
    assert [call["query"]["offset"] for call in calls] == [0, 200]
    assert calls[0]["query"]["is_archive"] == "false"
    assert calls[0]["api"] == "returns"


@respx.mock
async def test_archive_claims_marked_archived() -> None:
    stub = EgressStub(respx.mock)
    stub.on("GET", CLAIMS, body={"claims": [claim_row("22222222-2222-2222-2222-222222222222", status=2)], "total": 1})
    client = WBClaimsClient(make_gateway())

    claims = await client.claims(SELLER, archive=True)

    assert len(claims) == 1
    assert claims[0].is_archive and not claims[0].is_open
    assert stub.requests_to(CLAIMS)[0]["query"]["is_archive"] == "true"
