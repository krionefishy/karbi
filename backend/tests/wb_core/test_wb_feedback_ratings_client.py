from datetime import date
from decimal import Decimal

import respx

from backend.modules.wb_core.infrastructure.wb import WBAnalyticsClient
from backend.modules.wb_core.infrastructure.wb.analytics import FUNNEL_PAGE_LIMIT, FUNNEL_PATH
from backend.tests.egress_stub import EgressStub, make_gateway

SELLER = "seller-1"


def product(nm_id: object, rating: object) -> dict:
    return {"product": {"nmId": nm_id, "feedbackRating": rating, "productRating": 10}, "statistic": {}}


async def test_ratings_are_read_per_nm_id_and_junk_is_skipped() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            FUNNEL_PATH,
            body={
                "data": {
                    "products": [
                        product(1304405626, 4.9),
                        product(1199412447, 0),
                        product("bad", 4.5),
                        product(1001, None),
                        product(1002, "4.75"),
                        product(1003, 7),
                    ]
                }
            },
        )

        ratings = await WBAnalyticsClient(make_gateway()).feedback_ratings(SELLER, date(2026, 10, 6), date(2026, 11, 4))

    assert ratings == {"1304405626": Decimal("4.90"), "1002": Decimal("4.75")}
    envelope = stub.requests_to(FUNNEL_PATH)[-1]
    assert (envelope["api"], envelope["method"], envelope["seller_id"]) == ("analytics", "POST", SELLER)
    assert envelope["body"] == {
        "selectedPeriod": {"start": "2026-10-06", "end": "2026-11-04"},
        "limit": FUNNEL_PAGE_LIMIT,
        "offset": 0,
    }


async def test_a_full_page_asks_for_the_next_one() -> None:
    full = {"data": {"products": [product(index + 1, 4.8) for index in range(FUNNEL_PAGE_LIMIT)]}}
    last = {"data": {"products": [product(5000, 4.1)]}}
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", FUNNEL_PATH, side_effect=[(200, full), (200, last)])

        ratings = await WBAnalyticsClient(make_gateway()).feedback_ratings(SELLER, date(2026, 10, 6), date(2026, 11, 4))

    assert len(ratings) == FUNNEL_PAGE_LIMIT + 1
    assert [call["body"]["offset"] for call in stub.requests_to(FUNNEL_PATH)] == [0, FUNNEL_PAGE_LIMIT]
