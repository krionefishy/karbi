from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.platform.presentation.http import router as auth_router
from backend.modules.wb_core.application import SellerService
from backend.modules.wb_core.presentation.http import router as wb_sellers_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(wb_sellers_router)


@router.get("/automations")
@inject
async def automations(_: CurrentPrincipal, sellers: FromDishka[SellerService]) -> list[dict]:
    seller_count = len(await sellers.list_sellers())
    return [
        {
            "id": "wb-reviews",
            "title": "Мониторинг отзывов Wildberries",
            "description": "Ежедневные снимки отзывов по всем товарам и селлерам Wildberries.",
            "status": "active",
            "last_run_at": None,
            "seller_count": seller_count,
        },
        {
            "id": "marketplace-reports",
            "title": "Сводные отчёты маркетплейсов",
            "description": "Единая отчётность по продажам и остаткам.",
            "status": "coming_soon",
            "last_run_at": None,
            "seller_count": None,
        },
        {
            "id": "ozon-reviews",
            "title": "Мониторинг отзывов Ozon",
            "description": "Динамика рейтингов и отзывов по товарам Ozon.",
            "status": "coming_soon",
            "last_run_at": None,
            "seller_count": None,
        },
    ]


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
