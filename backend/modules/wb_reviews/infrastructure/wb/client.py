import asyncio
import hashlib
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb import (
    WBThrottle,
    WBThrottleTimeout,
    host_bucket,
    key_bucket,
    scope_for_key,
)
from backend.modules.wb_core.infrastructure.wb.observability import read_rate_limit

FEEDBACKS_BUCKET = "feedbacks"
# WB stops serving feedback pages somewhere around this offset. Failing loudly
# is deliberate: a snapshot silently cut off at the ceiling would be written to
# the database as if it were complete.
MAX_PAGINATION_DEPTH = 200_000


class WBFeedbackPermanentError(Exception):
    pass


class WBFeedbackTemporaryError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FeedbackProduct:
    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None


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
        request_interval_seconds: float = 1.0,
        max_retry_wait_seconds: float = 600.0,
        throttle: WBThrottle | None = None,
    ) -> None:
        if not 1 <= page_size <= 5000:
            raise ValueError("WB feedback page size must be between 1 and 5000")
        self.page_size = page_size
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.request_interval_seconds = request_interval_seconds
        self.max_retry_wait_seconds = max_retry_wait_seconds
        self.throttle = throttle
        self.logger = logging.getLogger("wb.feedbacks.client")

    async def aggregate(self, api_key: str) -> FeedbackAggregation:
        """Every feedback of the account, counted per nmID.

        Three passes are needed: unanswered, answered and the archive. A
        feedback that has been answered but not yet archived belongs to none of
        the other two buckets, so dropping the middle pass silently undercounts.
        Ids seen twice across passes are deduplicated.
        """
        counts: dict[str, list[int]] = {}
        products: dict[str, FeedbackProduct] = {}
        seen: set[bytes] = set()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            for path, extra_params in (
                ("/api/v1/feedbacks", {"isAnswered": "false"}),
                ("/api/v1/feedbacks", {"isAnswered": "true"}),
                ("/api/v1/feedbacks/archive", {}),
            ):
                await self._consume_pages(client, api_key, path, extra_params, counts, products, seen)
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
            if skip >= MAX_PAGINATION_DEPTH:
                raise WBFeedbackPermanentError(
                    f"WB Feedbacks API: пагинация {path} упёрлась в потолок {MAX_PAGINATION_DEPTH} отзывов, "
                    "снапшот был бы усечён"
                )
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
        imt_value = str(details.get("imtId") or "")
        products.setdefault(
            article,
            FeedbackProduct(
                article=article,
                vendor_code=str(details.get("supplierArticle") or ""),
                name=str(details.get("productName") or details.get("supplierArticle") or article),
                imt_id=int(imt_value) if imt_value.isdigit() else None,
            ),
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        path: str,
        api_key: str,
        params: dict[str, str],
    ) -> httpx.Response:
        attempt = 0
        waited_seconds = 0.0
        scope = scope_for_key(api_key)
        while True:
            if self.throttle is not None:
                try:
                    await self.throttle.acquire(
                        (key_bucket(FEEDBACKS_BUCKET), scope),
                        (host_bucket(FEEDBACKS_BUCKET), "all"),
                    )
                except WBThrottleTimeout as error:
                    raise WBFeedbackTemporaryError(f"WB Feedbacks API: {error}") from error
            try:
                response = await client.get(path, headers={"Authorization": api_key}, params=params)
            except httpx.RequestError as error:
                next_waited_seconds = await self._wait_before_retry(None, attempt, waited_seconds)
                if next_waited_seconds is None:
                    raise WBFeedbackTemporaryError(
                        f"Не удалось подключиться к WB Feedbacks API {self._retry_exhausted_message()}: {error}"
                    ) from error
                waited_seconds = next_waited_seconds
                attempt += 1
                continue
            snapshot = read_rate_limit(self.logger, response, path=path)
            if self.throttle is not None:
                self.throttle.observe(key_bucket(FEEDBACKS_BUCKET), scope, snapshot)
            if response.status_code in {400, 401, 402, 403, 413, 422}:
                raise WBFeedbackPermanentError(
                    f"WB Feedbacks API rejected the request with status {response.status_code}"
                )
            if response.status_code == 429 or response.status_code >= 500:
                next_waited_seconds = await self._wait_before_retry(response, attempt, waited_seconds)
                if next_waited_seconds is None:
                    raise WBFeedbackTemporaryError(
                        f"WB Feedbacks API временно недоступен {self._retry_exhausted_message()}: "
                        f"HTTP {response.status_code}"
                    )
                waited_seconds = next_waited_seconds
                attempt += 1
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise WBFeedbackTemporaryError(f"Ошибка WB Feedbacks API: {error}") from error
            if self.request_interval_seconds:
                await asyncio.sleep(self.request_interval_seconds)
            return response

    async def _wait_before_retry(
        self,
        response: httpx.Response | None,
        attempt: int,
        waited_seconds: float,
    ) -> float | None:
        remaining = self.max_retry_wait_seconds - waited_seconds
        if remaining <= 0:
            return None
        delay = min(self._retry_delay(response, attempt), remaining)
        await asyncio.sleep(delay)
        return waited_seconds + delay

    def _retry_exhausted_message(self) -> str:
        if self.max_retry_wait_seconds == 600:
            return "после 10 минут повторов"
        return f"после {self.max_retry_wait_seconds:g} секунд повторов"

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        """Wait exactly as long as WB asked, and guess only when it stayed silent."""
        fallback_steps = (5.0, 10.0, 20.0, 30.0, 60.0)
        fallback = fallback_steps[min(attempt, len(fallback_steps) - 1)]
        if response is None:
            return fallback + random.uniform(0.1, 1.0)
        retry_header = response.headers.get("X-Ratelimit-Retry") or response.headers.get("Retry-After")
        if retry_header is None:
            return fallback + random.uniform(0.1, 1.0)
        try:
            return max(float(retry_header), 0.0) + random.uniform(0.1, 1.0)
        except ValueError:
            return fallback + random.uniform(0.1, 1.0)
