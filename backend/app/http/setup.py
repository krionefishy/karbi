from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.http.middleware import AccessTokenMiddleware, RateLimitMiddleware
from backend.modules.platform.application import TokenService
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient, SlidingWindowRateLimiter


def setup_http_middleware(
    app: FastAPI,
    *,
    settings: Settings,
    redis: RedisClient,
    token_service: TokenService,
) -> None:
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
