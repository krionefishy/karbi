import logging

from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.http.client_ip import resolve_client_ip
from backend.storage.redis import SlidingWindowRateLimiter


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowRateLimiter,
        enabled: bool,
        limit: int,
        behind_trusted_proxy: bool,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._enabled = enabled
        self._limit = limit
        self._behind_trusted_proxy = behind_trusted_proxy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if not self._enabled or request.url.path.startswith("/api/v1/health/"):
            await self.app(scope, receive, send)
            return

        identity = resolve_client_ip(request, behind_trusted_proxy=self._behind_trusted_proxy)
        try:
            decision = await self._limiter.check(identity)
        except RedisError:
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
                headers["X-RateLimit-Limit"] = str(self._limit)
                headers["X-RateLimit-Remaining"] = str(decision.remaining)
            await send(message)

        await self.app(scope, receive, send_with_rate_limit_headers)
