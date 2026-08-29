from unittest.mock import AsyncMock

import pytest
import respx

from backend.modules.wb_reviews.infrastructure.wb import (
    WBFeedbackClient,
    WBFeedbackPermanentError,
    WBFeedbackTemporaryError,
)
from backend.tests.egress_stub import EgressStub, make_gateway

FEEDBACKS = "/api/v1/feedbacks"
ARCHIVE = "/api/v1/feedbacks/archive"
SELLER = "seller-1"


def feedback(feedback_id: str, article: int, rating: int, name: str = "Товар") -> dict:
    return {
        "id": feedback_id,
        "productValuation": rating,
        "productDetails": {
            "nmId": article,
            "imtId": 900 + article % 10,
            "supplierArticle": f"SKU-{article}",
            "productName": name,
        },
    }


def page(*feedbacks: dict) -> tuple[int, dict]:
    return 200, {"data": {"feedbacks": list(feedbacks)}}


def client(page_size: int = 5000) -> WBFeedbackClient:
    return WBFeedbackClient(make_gateway(), page_size=page_size)


async def test_feedback_client_reads_all_three_buckets_and_deduplicates() -> None:
    """Unanswered, answered and archive; an id seen twice is counted once."""
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            FEEDBACKS,
            side_effect=[
                page(feedback("a", 101, 5), feedback("b", 101, 1)),
                page(),
                page(feedback("e", 303, 3), feedback("f", 303, 3)),
                page(),
            ],
        )
        stub.on(
            "GET",
            ARCHIVE,
            side_effect=[
                page(feedback("a", 101, 5), feedback("c", 202, 4)),
                page(feedback("d", 101, 5)),
            ],
        )

        result = await client(page_size=2).aggregate(SELLER)

        live_calls = stub.requests_to(FEEDBACKS)
        assert len(live_calls) == 4
        assert len(stub.requests_to(ARCHIVE)) == 2
        assert [call["query"]["isAnswered"] for call in live_calls] == ["false", "false", "true", "true"]
        # Конверт шлюза несёт селлера, а не ключ.
        assert {call["seller_id"] for call in stub.calls} == {SELLER}

    assert result.counts == {"101": (1, 0, 0, 0, 2), "202": (0, 0, 0, 1, 0), "303": (0, 0, 2, 0, 0)}
    assert result.products["202"].vendor_code == "SKU-202"
    assert result.products["202"].imt_id == 902
    assert result.feedback_count == 6


async def test_feedback_client_ignores_feedback_without_valid_rating() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", FEEDBACKS, body={"data": {"feedbacks": [feedback("invalid", 101, 0)]}})
        stub.on("GET", ARCHIVE, body={"data": {"feedbacks": []}})

        result = await client().aggregate(SELLER)

    assert result.counts == {}
    assert result.feedback_count == 0


async def test_feedback_client_stops_after_rate_limit_retries(monkeypatch) -> None:
    """Ретраи 429 теперь живут в шлюзовом клиенте, а не в клиенте отзывов."""
    sleep = AsyncMock()
    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.egress.asyncio.sleep", sleep)
    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.egress.random.uniform", lambda *_: 0.1)

    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("GET", FEEDBACKS, status=429)

        with pytest.raises(WBFeedbackTemporaryError, match="HTTP 429"):
            await client().aggregate(SELLER)

        assert len(stub.requests_to(FEEDBACKS)) == 3

    assert sleep.await_count == 2
    # Без rate_limit в ответе шлюза действует запасная пауза в 5 секунд.
    assert [call.args[0] for call in sleep.await_args_list] == [pytest.approx(5.1), pytest.approx(5.1)]


async def test_feedback_client_retries_the_same_page_after_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.egress.asyncio.sleep", AsyncMock())

    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            FEEDBACKS,
            side_effect=[
                (429, None),
                page(feedback("a", 101, 5)),
                page(),
                page(),
            ],
        )
        stub.on("GET", ARCHIVE, body={"data": {"feedbacks": []}})

        result = await client(page_size=1).aggregate(SELLER)

        active_calls = stub.requests_to(FEEDBACKS)

    assert result.counts == {"101": (0, 0, 0, 0, 1)}
    assert len(active_calls) == 4
    assert [call["query"]["skip"] for call in active_calls] == ["0", "0", "1", "0"]


async def test_feedback_client_fails_loudly_at_the_pagination_ceiling(monkeypatch) -> None:
    """WB stops serving pages around 200k; a snapshot cut off there must not be
    written to the database as if it were complete."""
    monkeypatch.setattr("backend.modules.wb_reviews.infrastructure.wb.client.MAX_PAGINATION_DEPTH", 2)

    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            FEEDBACKS,
            side_effect=[
                page(feedback("a", 101, 5)),
                page(feedback("b", 101, 4)),
            ],
        )

        with pytest.raises(WBFeedbackPermanentError, match="потолок"):
            await client(page_size=1).aggregate(SELLER)

        # Two full pages exhaust the ceiling of two; the third request never happens.
        assert len(stub.requests_to(FEEDBACKS)) == 2
