import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_turnover.application import NotificationBotMissingError, TurnoverService
from backend.modules.wb_turnover.presentation.http.schemas import (
    InviteLinkResponse,
    RefreshResponse,
    TurnoverArticlesResponse,
)
from backend.modules.wb_turnover.presentation.http.utils import article_response
from backend.shared.settings import Settings

router = APIRouter(prefix="/wb/turnover", tags=["wb-turnover"])


@router.get("/sellers/{seller_id}/articles", response_model=TurnoverArticlesResponse)
@inject
async def seller_articles(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[TurnoverService],
    settings: FromDishka[Settings],
) -> TurnoverArticlesResponse:
    try:
        snapshot = await service.articles(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден") from error
    return TurnoverArticlesResponse(
        seller_id=seller_id,
        date=snapshot.date.isoformat() if snapshot.date else None,
        threshold_days=settings.turnover.threshold_days,
        window_days=settings.turnover.orders_window_days,
        articles=[article_response(item) for item in snapshot.articles],
    )


@router.post("/sellers/{seller_id}/invite-link", response_model=InviteLinkResponse)
@inject
async def invite_link(
    seller_id: uuid.UUID,
    principal: CurrentPrincipal,
    service: FromDishka[TurnoverService],
) -> InviteLinkResponse:
    """A fresh personal link to the notifications bot. Issuing one voids the previous."""
    try:
        invite = await service.invite_link(seller_id, created_by=principal.user_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации") from error
    except NotificationBotMissingError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Бот уведомлений ещё не зарегистрирован — сделайте это командой register_bot",
        ) from error
    return InviteLinkResponse(url=invite.url, expires_at=invite.expires_at.isoformat())


def refresh_response(state) -> RefreshResponse:
    return RefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        error=state.error,
    )


@router.post("/sellers/{seller_id}/refresh", response_model=RefreshResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh(
    seller_id: uuid.UUID,
    principal: CurrentPrincipal,
    service: FromDishka[TurnoverService],
) -> RefreshResponse:
    """Ask for an out-of-schedule collection. Pressing twice returns the same request."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации") from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=RefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[TurnoverService],
) -> RefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации") from error
    return refresh_response(state) if state else None
