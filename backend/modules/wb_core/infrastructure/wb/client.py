import asyncio
import random
from typing import Any

import httpx


class WBPermanentError(Exception):
    pass


class WBTemporaryError(Exception):
    pass


class WBContentClient:
    endpoint = "https://content-api.wildberries.ru/content/v2/get/cards/list"

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    async def get_articles(self, api_key: str) -> list[dict[str, str]]:
        cursor: dict[str, Any] = {"limit": 100}
        articles: list[dict[str, str]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while True:
                response = await self._request(client, api_key, cursor)
                payload = response.json()
                cards = payload.get("cards", [])
                articles.extend(
                    {
                        "article": str(card["nmID"]),
                        "vendor_code": str(card.get("vendorCode", "")),
                        "name": str(
                            card.get("title") or card.get("subjectName") or card.get("vendorCode") or card["nmID"]
                        ),
                    }
                    for card in cards
                )
                result_cursor = payload.get("cursor", {})
                if result_cursor.get("total", len(cards)) < cursor["limit"]:
                    break
                if not result_cursor.get("updatedAt") or not result_cursor.get("nmID"):
                    raise WBPermanentError("WB вернул некорректный курсор пагинации")
                cursor = {"limit": 100, "updatedAt": result_cursor["updatedAt"], "nmID": result_cursor["nmID"]}
                await asyncio.sleep(0.6)
        return articles

    async def _request(self, client: httpx.AsyncClient, api_key: str, cursor: dict[str, Any]) -> httpx.Response:
        for attempt in range(5):
            try:
                response = await client.post(
                    self.endpoint,
                    headers={"Authorization": api_key},
                    json={
                        "settings": {
                            "sort": {"ascending": True},
                            "filter": {"withPhoto": -1},
                            "cursor": cursor,
                        }
                    },
                )
            except httpx.RequestError as error:
                if attempt == 4:
                    raise WBTemporaryError(
                        f"Не удалось подключиться к WB Content API после 5 попыток: {error}"
                    ) from error
                await asyncio.sleep(self._retry_delay(None, attempt))
                continue
            if response.status_code in {401, 403}:
                raise WBPermanentError("API-ключ недействителен или не имеет доступа к категории Контент")
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
        fallback = float(min(2**attempt, 30))
        try:
            requested = max(float(response.headers.get("Retry-After", fallback)), 0.0) if response else fallback
        except ValueError:
            requested = fallback
        return max(requested, fallback) + random.uniform(0.1, 0.5)
