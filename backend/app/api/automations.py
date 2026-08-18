from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter

from backend.app.api.schemas import AutomationResponse
from backend.app.api.utils import automation_catalog
from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.shared.settings import Settings

router = APIRouter(tags=["automations"])


@router.get("/automations", response_model=list[AutomationResponse])
@inject
async def automations(
    _: CurrentPrincipal,
    reviews: FromDishka[ReviewSyncService],
    settings: FromDishka[Settings],
) -> list[AutomationResponse]:
    return automation_catalog(await reviews.overview(), settings)
