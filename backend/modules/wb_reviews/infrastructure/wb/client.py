import asyncio
import hashlib
import random
from dataclasses import dataclass
from typing import Any

import httpx


class WBFeedbackPermanentError(Exception):
    pass


class WBFeedbackTemporaryError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FeedbackProduct:
    article: str
    vendor_code: str
    name: str


@dataclass(frozen=True, slots=True)
class FeedbackAggregation:
    counts: dict[str, tuple[int, int, int, int, int]]
    products: dict[str, FeedbackProduct]
    feedback_count: int


class WBFeedbackClient:
    base_url = "https://feedbacks-api.wildberries.ru"

    def __init__(
        self,
        page_size: int = 5000,
        timeout_seconds: float = 60.0,
        request_interval_seconds: float = 0.5,
    ) -> None:
        if not 1 <= page_size <= 5000:
            raise ValueError("WB feedback page size must be between 1 and 5000")
        self.page_size = page_size
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.request_interval_seconds = request_interval_seconds

    async def aggregate(self, api_key: str) -> FeedbackAggregation:
        counts: dict[str, list[int]] = {}
        products: dict[str, FeedbackProduct] = {}
        seen: set[bytes] = set()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            await self._consume_pages(
                client,
                api_key,
                "/api/v1/feedbacks",
                {"isAnswered": "false"},
                counts,
                products,
                seen,
            )
            await self._consume_pages(
                client,
                api_key,
                "/api/v1/feedbacks/archive",
                {},
                counts,
                products,
                seen,
            )
        return FeedbackAggregation(
            counts={
                article: (values[0], values[1], values[2], values[3], values[4]) for article, values in counts.items()
            },
            products=products,
            feedback_count=len(seen),
        )

    async def _consume_pages(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        path: str,
        extra_params: dict[str, str],
        counts: dict[str, list[int]],
        products: dict[str, FeedbackProduct],
        seen: set[bytes],
    ) -> None:
        skip = 0
        while True:
            response = await self._request(
                client,
                path,
                api_key,
                {**extra_params, "take": str(self.page_size), "skip": str(skip), "order": "dateAsc"},
            )
            payload = response.json()
            if payload.get("error"):
                raise WBFeedbackPermanentError(str(payload.get("errorText") or "WB rejected feedback request"))
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise WBFeedbackPermanentError("WB returned an invalid feedback payload")
            feedbacks = data.get("feedbacks", [])
            if not isinstance(feedbacks, list):
                raise WBFeedbackPermanentError("WB returned an invalid feedback list")
            for feedback in feedbacks:
                self._accumulate(feedback, counts, products, seen)
            page_length = len(feedbacks)
            del feedbacks, payload, response
            if page_length < self.page_size:
                return
            skip += page_length

    @staticmethod
    def _accumulate(
        feedback: dict[str, Any],
        counts: dict[str, list[int]],
        products: dict[str, FeedbackProduct],
        seen: set[bytes],
    ) -> None:
        feedback_id = feedback.get("id")
        details = feedback.get("productDetails") or {}
        article_value = details.get("nmId")
        rating_value = feedback.get("productValuation")
        if not feedback_id or article_value is None or not isinstance(rating_value, int) or not 1 <= rating_value <= 5:
            return
        digest = hashlib.blake2b(str(feedback_id).encode(), digest_size=16).digest()
        if digest in seen:
            return
        seen.add(digest)
        article = str(article_value)
        counts.setdefault(article, [0, 0, 0, 0, 0])[rating_value - 1] += 1
        products.setdefault(
            article,
            FeedbackProduct(
                article=article,
                vendor_code=str(details.get("supplierArticle") or ""),
                name=str(details.get("productName") or details.get("supplierArticle") or article),
            ),
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        path: str,
        api_key: str,
        params: dict[str, str],
    ) -> httpx.Response:
        for attempt in range(5):
            try:
                response = await client.get(path, headers={"Authorization": api_key}, params=params)
            except httpx.RequestError as error:
                if attempt == 4:
                    raise WBFeedbackTemporaryError(
                        f"Не удалось подключиться к WB Feedbacks API после 5 попыток: {error}"
                    ) from error
                await asyncio.sleep(self._retry_delay(None, attempt))
                continue
            if response.status_code in {400, 401, 402, 403, 413, 422}:
                raise WBFeedbackPermanentError(
                    f"WB Feedbacks API rejected the request with status {response.status_code}"
                )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    raise WBFeedbackTemporaryError(
                        f"WB Feedbacks API временно недоступен после 5 попыток: HTTP {response.status_code}"
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise WBFeedbackTemporaryError(f"Ошибка WB Feedbacks API: {error}") from error
            await asyncio.sleep(self.request_interval_seconds)
            return response
        raise RuntimeError("WB feedback request retry loop exhausted")

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        fallback = float(min(2**attempt, 30))
        try:
            requested = max(float(response.headers.get("Retry-After", fallback)), 0.0) if response else fallback
        except ValueError:
            requested = fallback
        return max(requested, fallback) + random.uniform(0.1, 0.5)
