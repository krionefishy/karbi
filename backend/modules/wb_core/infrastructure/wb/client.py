import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb.observability import read_rate_limit
from backend.modules.wb_core.infrastructure.wb.throttle import (
    WBThrottle,
    WBThrottleTimeout,
    host_bucket,
    key_bucket,
    scope_for_key,
)

CONTENT_BUCKET = "content"
PAGE_LIMIT = 100


class WBPermanentError(Exception):
    pass


class WBTemporaryError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CatalogCard:
    """One WB card. `article` is the nmID; `imt_id` is the склейка it belongs to."""

    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None
    brand: str = ""
    subject_id: int | None = None
    subject_name: str = ""
    photo_url: str = ""
    sizes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    active: list[CatalogCard]
    archived: list[CatalogCard]
    archived_available: bool


class WBContentClient:
    endpoint = "https://content-api.wildberries.ru/content/v2/get/cards/list"
    trash_endpoint = "https://content-api.wildberries.ru/content/v2/get/cards/trash"

    def __init__(self, timeout_seconds: float = 30.0, throttle: WBThrottle | None = None) -> None:
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.throttle = throttle
        self.logger = logging.getLogger("wb.content.client")

    async def get_catalog(self, api_key: str) -> CatalogSnapshot:
        """Both halves of the account: cards in sale and cards the seller archived.

        `cards/list` never returns archived cards, so a товар moved to the корзина
        would otherwise look as if it had vanished from WB entirely.
        """
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            active = await self._consume(client, api_key, self.endpoint, {"withPhoto": -1})
            try:
                archived = await self._consume(client, api_key, self.trash_endpoint, None)
            except (WBPermanentError, WBTemporaryError) as error:
                self.logger.warning("wb_trash_unavailable", extra={"error": str(error)})
                return CatalogSnapshot(active=active, archived=[], archived_available=False)
        return CatalogSnapshot(active=active, archived=archived, archived_available=True)

    async def get_articles(self, api_key: str) -> list[CatalogCard]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await self._consume(client, api_key, self.endpoint, {"withPhoto": -1})

    async def _consume(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        endpoint: str,
        card_filter: dict[str, Any] | None,
    ) -> list[CatalogCard]:
        cursor: dict[str, Any] = {"limit": PAGE_LIMIT}
        cards: list[CatalogCard] = []
        while True:
            response = await self._request(client, endpoint, api_key, cursor, card_filter)
            payload = response.json()
            page = payload.get("cards") or []
            cards.extend(self._card(item) for item in page if item.get("nmID") is not None)
            result_cursor = payload.get("cursor") or {}
            if int(result_cursor.get("total", len(page))) < PAGE_LIMIT:
                return cards
            # WB names the continuation field differently per endpoint (updatedAt for
            # the catalog, trashedAt for the корзина), so echo back whatever it sent.
            continuation = {key: value for key, value in result_cursor.items() if key != "total"}
            if not continuation:
                raise WBPermanentError("WB вернул некорректный курсор пагинации")
            cursor = {"limit": PAGE_LIMIT, **continuation}

    @staticmethod
    def _card(card: dict[str, Any]) -> CatalogCard:
        vendor_code = str(card.get("vendorCode") or "")
        article = str(card["nmID"])
        return CatalogCard(
            article=article,
            vendor_code=vendor_code,
            name=str(card.get("title") or card.get("subjectName") or vendor_code or article),
            imt_id=int(card["imtID"]) if str(card.get("imtID") or "").isdigit() else None,
            brand=str(card.get("brand") or ""),
            subject_id=int(card["subjectID"]) if str(card.get("subjectID") or "").isdigit() else None,
            subject_name=str(card.get("subjectName") or ""),
            photo_url=WBContentClient._photo(card.get("photos")),
            sizes=WBContentClient._sizes(card.get("sizes")),
        )

    @staticmethod
    def _photo(photos: Any) -> str:
        if not isinstance(photos, list):
            return ""
        for photo in photos:
            if isinstance(photo, str):
                return photo
            if isinstance(photo, dict):
                for size in ("c246x328", "square", "big", "c516x688", "tm"):
                    value = photo.get(size)
                    if isinstance(value, str) and value:
                        return value
        return ""

    @staticmethod
    def _sizes(sizes: Any) -> list[dict[str, Any]]:
        if not isinstance(sizes, list):
            return []
        collected = []
        for size in sizes:
            if not isinstance(size, dict):
                continue
            skus = size.get("skus")
            collected.append(
                {
                    "chrt_id": size.get("chrtID"),
                    "tech_size": str(size.get("techSize") or ""),
                    "skus": [str(sku) for sku in skus] if isinstance(skus, list) else [],
                }
            )
        return collected

    async def _request(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        api_key: str,
        cursor: dict[str, Any],
        card_filter: dict[str, Any] | None,
    ) -> httpx.Response:
        settings: dict[str, Any] = {"sort": {"ascending": True}, "cursor": cursor}
        if card_filter is not None:
            settings["filter"] = card_filter
        scope = scope_for_key(api_key)
        for attempt in range(5):
            if self.throttle is not None:
                try:
                    await self.throttle.acquire(
                        (key_bucket(CONTENT_BUCKET), scope),
                        (host_bucket(CONTENT_BUCKET), "all"),
                    )
                except WBThrottleTimeout as error:
                    raise WBTemporaryError(f"WB Content API: {error}") from error
            try:
                response = await client.post(
                    endpoint,
                    headers={"Authorization": api_key},
                    json={"settings": settings},
                )
            except httpx.RequestError as error:
                if attempt == 4:
                    raise WBTemporaryError(
                        f"Не удалось подключиться к WB Content API после 5 попыток: {error}"
                    ) from error
                await asyncio.sleep(self._retry_delay(None, attempt))
                continue
            snapshot = read_rate_limit(self.logger, response, path=endpoint)
            if self.throttle is not None:
                self.throttle.observe(key_bucket(CONTENT_BUCKET), scope, snapshot)
            if response.status_code in {401, 403}:
                raise WBPermanentError("API-ключ недействителен или не имеет доступа к категории Контент")
            if response.status_code == 404:
                raise WBPermanentError(f"WB Content API не знает метод {endpoint}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 4:
                    raise WBTemporaryError(
                        f"WB Content API временно недоступен после 5 попыток: HTTP {response.status_code}"
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise WBTemporaryError(f"Ошибка WB Content API: {error}") from error
            return response
        raise RuntimeError("WB request retry loop exhausted")

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        """Wait exactly as long as WB asked, and guess only when it stayed silent."""
        fallback = float(min(2**attempt, 30))
        if response is None:
            return fallback + random.uniform(0.1, 0.5)
        retry_header = response.headers.get("X-Ratelimit-Retry") or response.headers.get("Retry-After")
        if retry_header is None:
            return fallback + random.uniform(0.1, 0.5)
        try:
            return max(float(retry_header), 0.0) + random.uniform(0.1, 0.5)
        except ValueError:
            return fallback + random.uniform(0.1, 0.5)
