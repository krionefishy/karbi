import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.api.schemas import AutomationResponse, AutomationSellerAttach
from backend.app.api.utils import automation_catalog
from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import (
    AutomationNotFoundError,
    DuplicateCredentialError,
    SellerArchivedError,
    SellerNotFoundError,
    SellerService,
)
from backend.modules.wb_core.presentation.http.schemas import SellerResponse
from backend.modules.wb_core.presentation.http.utils import archived_conflict, not_found, seller_responses
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_turnover.application import TurnoverService
from backend.shared.settings import Settings

router = APIRouter(tags=["automations"])


def unknown_automation() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Автоматизация не найдена")


@router.get("/automations", response_model=list[AutomationResponse])
@inject
async def automations(
    _: CurrentPrincipal,
    reviews: FromDishka[ReviewSyncService],
    turnover: FromDishka[TurnoverService],
    settings: FromDishka[Settings],
) -> list[AutomationResponse]:
    return automation_catalog(await reviews.overview(), await turnover.overview(), settings)


@router.get("/automations/{automation_id}/sellers", response_model=list[SellerResponse])
@inject
async def automation_sellers(
    automation_id: str, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> list[SellerResponse]:
    try:
        enrolled = await service.enrolled(automation_id)
    except AutomationNotFoundError as error:
        raise unknown_automation() from error
    return await seller_responses(service, enrolled)


@router.post("/automations/{automation_id}/sellers", response_model=SellerResponse, status_code=status.HTTP_201_CREATED)
@inject
async def attach_seller(
    automation_id: str,
    payload: AutomationSellerAttach,
    _: CurrentPrincipal,
    service: FromDishka[SellerService],
) -> SellerResponse:
    """Connecting is the only way into an automation; creating a seller here is a shortcut."""
    try:
        service.enrollment(automation_id)
        seller_id = payload.seller_id
        if seller_id is None:
            created = await service.create(str(payload.name), str(payload.api_key.get_secret_value()))  # type: ignore[union-attr]
            seller_id = created.id
        seller = await service.enroll(automation_id, seller_id)
    except AutomationNotFoundError as error:
        raise unknown_automation() from error
    except SellerNotFoundError as error:
        raise not_found() from error
    except SellerArchivedError as error:
        raise archived_conflict() from error
    except DuplicateCredentialError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот API-ключ уже используется") from error
    return (await seller_responses(service, [seller]))[0]


@router.delete("/automations/{automation_id}/sellers/{seller_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def detach_seller(
    automation_id: str, seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> Response:
    """Disconnect from this automation only — the seller and his data stay."""
    try:
        await service.unenroll(automation_id, seller_id)
    except AutomationNotFoundError as error:
        raise unknown_automation() from error
    except SellerNotFoundError as error:
        raise not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
