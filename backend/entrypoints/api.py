from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.infrastructure.database import Database
from backend.infrastructure.logging import configure_logging
from backend.infrastructure.redis import RedisClient
from backend.infrastructure.redis.rate_limiter import SlidingWindowRateLimiter


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    database = Database(settings.database_url)
    redis = RedisClient(settings.redis_url)
    limiter = SlidingWindowRateLimiter(
        redis_client=redis,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await redis.connect()
        application.state.database = database
        application.state.redis = redis
        yield
        await redis.disconnect()
        await database.disconnect()

    application = FastAPI(title="Karbi API", version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path in {"/api/v1/health/live", "/api/v1/health/ready"}:
            return await call_next(request)
        forwarded_for = request.headers.get("x-forwarded-for")
        direct_ip = request.client.host if request.client is not None else "unknown"
        identity = forwarded_for.split(",", 1)[0].strip() if forwarded_for else direct_ip
        decision = await limiter.check(identity)
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests"},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
        return await call_next(request)

    @application.get("/api/v1/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/v1/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        database_ready = await database.ping()
        redis_ready = await redis.ping()
        status_code = 200 if database_ready and redis_ready else 503
        return JSONResponse(
            status_code=status_code,
            content={"database": database_ready, "redis": redis_ready},
        )

    return application


app = create_app()
