from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from backend.modules.wb_reviews.infrastructure.wb import WBFeedbackClient, WBFeedbackTemporaryError


def feedback(feedback_id: str, article: int, rating: int, name: str = "Товар") -> dict:
    return {
        "id": feedback_id,
        "productValuation": rating,
        "productDetails": {
            "nmId": article,
            "supplierArticle": f"SKU-{article}",
            "productName": name,
        },
    }


async def test_feedback_client_reads_active_and_archive_pages_and_deduplicates(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("backend.modules.wb_reviews.infrastructure.wb.client.asyncio.sleep", sleep)

    with respx.mock(base_url="https://feedbacks-api.wildberries.ru") as router:
        active_route = router.get("/api/v1/feedbacks").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"feedbacks": [feedback("a", 101, 5), feedback("b", 101, 1)]}},
                ),
                httpx.Response(200, json={"data": {"feedbacks": []}}),
            ]
        )
        archive_route = router.get("/api/v1/feedbacks/archive").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={"data": {"feedbacks": [feedback("a", 101, 5), feedback("c", 202, 4)]}},
                ),
                httpx.Response(200, json={"data": {"feedbacks": [feedback("d", 101, 5)]}}),
            ]
        )

        result = await WBFeedbackClient(page_size=2).aggregate("secret")
        assert active_route.call_count == 2
        assert archive_route.call_count == 2

    assert result.counts == {"101": (1, 0, 0, 0, 2), "202": (0, 0, 0, 1, 0)}
    assert result.products["202"].vendor_code == "SKU-202"
    assert result.feedback_count == 4


async def test_feedback_client_ignores_feedback_without_valid_rating(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.modules.wb_reviews.infrastructure.wb.client.asyncio.sleep",
        AsyncMock(),
    )
    with respx.mock(base_url="https://feedbacks-api.wildberries.ru") as router:
        router.get("/api/v1/feedbacks").respond(
            200,
            json={"data": {"feedbacks": [feedback("invalid", 101, 0)]}},
        )
        router.get("/api/v1/feedbacks/archive").respond(200, json={"data": {"feedbacks": []}})

        result = await WBFeedbackClient().aggregate("secret")

    assert result.counts == {}
    assert result.feedback_count == 0


async def test_feedback_client_stops_after_rate_limit_retries(monkeypatch) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr("backend.modules.wb_reviews.infrastructure.wb.client.asyncio.sleep", sleep)
    monkeypatch.setattr("backend.modules.wb_reviews.infrastructure.wb.client.random.uniform", lambda *_: 0.1)

    with respx.mock(base_url="https://feedbacks-api.wildberries.ru") as router:
        route = router.get("/api/v1/feedbacks").respond(429, headers={"Retry-After": "1"})

        with pytest.raises(WBFeedbackTemporaryError, match="после 5 попыток"):
            await WBFeedbackClient().aggregate("secret")

    assert route.call_count == 5
    assert sleep.await_count == 4
