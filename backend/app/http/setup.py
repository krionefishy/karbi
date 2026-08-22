from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.http.middleware import AccessTokenMiddleware, RateLimitMiddleware
from backend.modules.platform.application import TokenService
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient, SlidingWindowRateLimiter


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Answer 422 without echoing what was sent.

    FastAPI's default handler returns the offending value inside `detail`. On
    the bot registration route that value is a messenger token, which would come
    straight back to the caller and into any log that records response bodies.
    """
    errors: list[dict[str, Any]] = []
    if isinstance(exc, RequestValidationError):
        errors = [{"loc": list(error.get("loc", ())), "msg": error.get("msg", "invalid")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def setup_http_middleware(
    app: FastAPI,
    *,
    settings: Settings,
    redis: RedisClient,
    token_service: TokenService,
) -> None:
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(AccessTokenMiddleware, decoder=token_service)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowRateLimiter(
            redis_client=redis,
            limit=settings.rate_limit.requests,
            window_seconds=settings.rate_limit.window_seconds,
        ),
        enabled=settings.rate_limit.enabled,
        limit=settings.rate_limit.requests,
        behind_trusted_proxy=settings.app.trust_proxy_headers,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.app.cors_origins),
        allow_credentials=bool(settings.app.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
