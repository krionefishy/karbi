import uuid
from dataclasses import asdict

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_card_documents.application import (
    CardDocumentsService,
    DocumentsBoard,
    NotificationBotMissingError,
    RefreshRequest,
)
from backend.modules.wb_card_documents.presentation.http.schemas import (
    CardDocumentItemResponse,
    CardDocumentsBoardResponse,
    CardDocumentsInviteLinkResponse,
    CardDocumentsRefreshResponse,
    CardDocumentsSummaryResponse,
    CardDocumentState,
)
from backend.modules.wb_core.application import SellerNotFoundError

router = APIRouter(prefix="/wb/card-documents", tags=["wb-card-documents"])


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def board_response(board: DocumentsBoard) -> CardDocumentsBoardResponse:
    return CardDocumentsBoardResponse(
        collected_at=board.collected_at,
        last_error=board.last_error,
        stale=board.stale,
        summary=CardDocumentsSummaryResponse(**asdict(board.summary)),
        items=[CardDocumentItemResponse(**asdict(row)) for row in board.items],
    )


def refresh_response(state: RefreshRequest) -> CardDocumentsRefreshResponse:
    return CardDocumentsRefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        error=state.error,
    )


@router.get("/sellers/{seller_id}", response_model=CardDocumentsBoardResponse)
@inject
async def seller_documents(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[CardDocumentsService],
    state: CardDocumentState | None = None,
) -> CardDocumentsBoardResponse:
    """Все карточки кабинета с состоянием документов, проблемные первыми; `state` сужает только `items`."""
    try:
        board = await service.board(seller_id, state)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return board_response(board)


@router.post(
    "/sellers/{seller_id}/refresh", response_model=CardDocumentsRefreshResponse, status_code=status.HTTP_202_ACCEPTED
)
@inject
async def request_refresh(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[CardDocumentsService]
) -> CardDocumentsRefreshResponse:
    """Сбор вне расписания. Повторное нажатие возвращает тот же запрос."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=CardDocumentsRefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[CardDocumentsService]
) -> CardDocumentsRefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state) if state else None


@router.post("/sellers/{seller_id}/invite-link", response_model=CardDocumentsInviteLinkResponse)
@inject
async def invite_link(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[CardDocumentsService]
) -> CardDocumentsInviteLinkResponse:
    """Свежая персональная ссылка на бота документов. Выпуск новой гасит прежнюю."""
    try:
        invite = await service.invite_link(seller_id, created_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except NotificationBotMissingError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Бот документов ещё не зарегистрирован — добавьте его в админке на странице «Боты»",
        ) from error
    return CardDocumentsInviteLinkResponse(url=invite.url, expires_at=invite.expires_at.isoformat())
