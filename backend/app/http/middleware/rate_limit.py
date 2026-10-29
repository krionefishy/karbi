import logging
from dataclasses import dataclass

from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.http.client_ip import resolve_client_ip
from backend.storage.redis import SlidingWindowRateLimiter


@dataclass(frozen=True, slots=True)
class RelaxedRateLimit:
    """Свой, повышенный бюджет на IP для маршрутов, которые офис за одним адресом шлёт часто.

    Снять лимит совсем нельзя: тело multipart читается до авторизации, без него любой может
    гнать мегабайты на api. Точное совпадение с `excluded` остаётся под общим лимитом.
    """

    limiter: SlidingWindowRateLimiter
    limit: int
    prefixes: tuple[str, ...]
    excluded: tuple[str, ...] = ()

    def covers(self, path: str) -> bool:
        return path not in self.excluded and path.startswith(self.prefixes)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowRateLimiter,
        enabled: bool,
        limit: int,
        behind_trusted_proxy: bool,
        relaxed: tuple[RelaxedRateLimit, ...] = (),
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._enabled = enabled
        self._limit = limit
        self._behind_trusted_proxy = behind_trusted_proxy
        self._relaxed = relaxed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path
        if not self._enabled or path.startswith("/api/v1/health/"):
            await self.app(scope, receive, send)
            return

        limiter, limit = self._limiter, self._limit
        for relaxed in self._relaxed:
            if relaxed.covers(path):
                limiter, limit = relaxed.limiter, relaxed.limit
                break

        identity = resolve_client_ip(request, behind_trusted_proxy=self._behind_trusted_proxy)
        try:
            decision = await limiter.check(identity)
        except (RedisError, RuntimeError):
            logging.getLogger("rate_limit").exception("rate_limit_backend_unavailable")
            response = JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable"})
            await response(scope, receive, send)
            return

        if not decision.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
            await response(scope, receive, send)
            return

        async def send_with_rate_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-RateLimit-Limit"] = str(limit)
                headers["X-RateLimit-Remaining"] = str(decision.remaining)
            await send(message)

        await self.app(scope, receive, send_with_rate_limit_headers)
