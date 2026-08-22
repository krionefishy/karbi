from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.api.automations import router as automations_router
from backend.modules.platform.presentation.http import admin_users_router
from backend.modules.platform.presentation.http import router as auth_router
from backend.modules.wb_core.presentation.http import router as wb_sellers_router
from backend.modules.wb_reviews.presentation.http import router as wb_reviews_router
from backend.modules.wb_turnover.presentation.http import router as wb_turnover_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(admin_users_router)
router.include_router(wb_sellers_router)
router.include_router(wb_reviews_router)
router.include_router(wb_turnover_router)
router.include_router(automations_router)


@router.get("/health/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", include_in_schema=False)
async def ready(request: Request) -> JSONResponse:
    database_ready = await request.app.state.database.ping()
    redis_ready = await request.app.state.redis.ping()
    status_code = 200 if database_ready and redis_ready else 503
    return JSONResponse(
        status_code=status_code,
        content={"database": database_ready, "redis": redis_ready},
    )
