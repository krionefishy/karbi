import asyncio
import logging
import random
from typing import Any

import httpx

from backend.modules.wb_core.infrastructure.wb import (
    WBPermanentError,
    WBTemporaryError,
    WBThrottle,
    WBThrottleTimeout,
    host_bucket,
    key_bucket,
    scope_for_key,
)
from backend.modules.wb_core.infrastructure.wb.observability import read_rate_limit

ATTEMPTS = 5


class WBJsonClient:
    """Shared request loop for the JSON APIs this automation reads.

    Same rules as the catalog and feedback clients: pace from the
    `X-Ratelimit-*` headers of every response, wait exactly as long as WB asked,
    and turn a spent budget into a temporary error instead of parking the worker.
    """

    bucket = "statistics"
    api_name = "WB API"
    # Category of the WB token this client needs. A key is issued per category,
    # so "недействителен" is the wrong thing to tell someone whose key simply
    # lacks one checkbox.
    category = ""

    def __init__(self, timeout_seconds: float = 60.0, throttle: WBThrottle | None = None) -> None:
        self.timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self.throttle = throttle
        self.logger = logging.getLogger(f"wb.{self.bucket}.client")

    async def request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        api_key: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        scope = scope_for_key(api_key)
        for attempt in range(ATTEMPTS):
            await self._pace(scope)
            try:
                response = await client.request(
                    method, url, headers={"Authorization": api_key}, params=params, json=json
                )
            except httpx.RequestError as error:
                if attempt == ATTEMPTS - 1:
                    raise WBTemporaryError(f"{self.api_name} недоступен: {error}") from error
                await asyncio.sleep(self._retry_delay(None, attempt))
                continue
            snapshot = read_rate_limit(self.logger, response, path=url)
            if self.throttle is not None:
                self.throttle.observe(key_bucket(self.bucket), scope, snapshot)
            if response.status_code in {401, 403}:
                raise WBPermanentError(self._access_error())
            if response.status_code == 404:
                raise WBPermanentError(f"{self.api_name} не знает метод {url}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == ATTEMPTS - 1:
                    raise WBTemporaryError(f"{self.api_name} отвечает HTTP {response.status_code}")
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue
            if response.status_code >= 400:
                raise WBPermanentError(f"{self.api_name} отклонил запрос: HTTP {response.status_code}")
            return self._payload(response)
        raise RuntimeError("WB request retry loop exhausted")

    def _access_error(self) -> str:
        if self.category:
            return f"{self.api_name}: ключ не имеет доступа к категории «{self.category}»"
        return f"{self.api_name}: ключ недействителен или не имеет доступа"

    async def _pace(self, scope: str) -> None:
        if self.throttle is None:
            return
        try:
            await self.throttle.acquire((key_bucket(self.bucket), scope), (host_bucket(self.bucket), "all"))
        except WBThrottleTimeout as error:
            raise WBTemporaryError(f"{self.api_name}: {error}") from error

    def _payload(self, response: httpx.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise WBTemporaryError(f"{self.api_name} вернул не JSON") from error

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        fallback = float(min(2**attempt, 30))
        header = None
        if response is not None:
            header = response.headers.get("X-Ratelimit-Retry") or response.headers.get("Retry-After")
        if header is None:
            return fallback + random.uniform(0.1, 0.5)
        try:
            return max(float(header), 0.0) + random.uniform(0.1, 0.5)
        except ValueError:
            return fallback + random.uniform(0.1, 0.5)
