from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.app.api.automations import router as automations_router
from backend.app.http.principals import require_service
from backend.modules.fin_reports.presentation.http import router as fin_reports_router
from backend.modules.notifications.presentation.http import admin_bots_router
from backend.modules.platform.presentation.http import admin_users_router
from backend.modules.platform.presentation.http import router as auth_router
from backend.modules.wb_box_stickers.presentation.http import packing_router as wb_box_packing_router
from backend.modules.wb_box_stickers.presentation.http import router as wb_box_stickers_router
from backend.modules.wb_card_checklist.presentation.http import router as wb_card_checklist_router
from backend.modules.wb_card_documents.presentation.http import router as wb_card_documents_router
from backend.modules.wb_core.presentation.http import router as wb_sellers_router
from backend.modules.wb_fbs_distribution.presentation.http import onec_router
from backend.modules.wb_fbs_distribution.presentation.http import router as wb_fbs_distribution_router
from backend.modules.wb_fbs_penalties.presentation.http import router as wb_fbs_penalties_router
from backend.modules.wb_fbs_stocks.presentation.http import router as wb_fbs_stocks_router
from backend.modules.wb_podsort.presentation.http import router as wb_podsort_router
from backend.modules.wb_returns.presentation.http import extension_router as wb_returns_extension_router
from backend.modules.wb_returns.presentation.http import router as wb_returns_router
from backend.modules.wb_review_chats.presentation.http import router as wb_review_chats_router
from backend.modules.wb_reviews.presentation.http import router as wb_reviews_router
from backend.modules.wb_support_bot.presentation.http import router as wb_support_bot_router
from backend.modules.wb_turnover.presentation.http import router as wb_turnover_router

router = APIRouter()


SERVICE_ROUTERS: list[tuple[APIRouter, str]] = []
OPEN_ROUTERS: list[APIRouter] = []


def include_service(service_router: APIRouter, service_id: str) -> None:
    """Рабочие роутеры подключаются только так: доступ к сервису проверяется на всём роутере сразу."""
    SERVICE_ROUTERS.append((service_router, service_id))
    router.include_router(service_router, dependencies=[Depends(require_service(service_id))])


def include_open(open_router: APIRouter) -> None:
    """Роутер без доступа к сервису: вход, админка, машинные токены, общие реестры.

    Список таких роутеров зафиксирован в `tests/platform/test_service_access.py`:
    новый открытый роутер придётся назвать там явно.
    """
    OPEN_ROUTERS.append(open_router)
    router.include_router(open_router)


include_open(auth_router)
include_open(admin_users_router)
include_open(admin_bots_router)
include_open(wb_sellers_router)
include_service(wb_reviews_router, "wb-reviews")
include_service(wb_turnover_router, "wb-turnover")
include_service(wb_fbs_distribution_router, "wb-fbs-stocks")
include_open(onec_router)
include_service(wb_card_checklist_router, "wb-card-checklist")
include_service(wb_card_documents_router, "wb-card-documents")
include_service(wb_fbs_stocks_router, "wb-fbs-stocks")
include_service(wb_fbs_penalties_router, "wb-fbs-penalties")
include_service(wb_review_chats_router, "wb-review-chats")
include_service(wb_support_bot_router, "wb-support-bot")
include_service(wb_returns_router, "wb-returns")
include_open(wb_returns_extension_router)
include_service(wb_podsort_router, "wb-podsort")
include_service(wb_box_stickers_router, "wb-box-stickers")
include_service(wb_box_packing_router, "wb-box-stickers")
include_service(fin_reports_router, "fin-reports")
include_open(automations_router)


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
