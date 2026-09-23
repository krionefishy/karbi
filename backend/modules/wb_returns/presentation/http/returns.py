import uuid
from datetime import datetime

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_returns.application import (
    ClaimView,
    ExtensionService,
    ExtensionView,
    NotificationBotMissingError,
    RefreshRequest,
    ReturnsService,
    ReturnsView,
    ReturnView,
)
from backend.modules.wb_returns.presentation.http.schemas import (
    ClaimResponse,
    ExtensionInstallResponse,
    ExtensionResponse,
    InviteLinkResponse,
    PairingCodeResponse,
    RefreshResponse,
    ReturnResponse,
    ReturnsResponse,
)

router = APIRouter(prefix="/wb/returns", tags=["wb-returns"])


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def return_response(view: ReturnView) -> ReturnResponse:
    item = view.item
    return ReturnResponse(
        shk_id=item.shk_id,
        sticker_id=item.sticker_id,
        srid=item.srid,
        order_id=item.order_id,
        nm_id=item.nm_id,
        barcode=item.barcode,
        title=item.title,
        brand=item.brand,
        subject_name=item.subject_name,
        tech_size=item.tech_size,
        return_type=item.return_type,
        reason=item.reason,
        status=item.status,
        status_key=item.status_key,
        status_title=view.status_title,
        dst_office_id=item.dst_office_id,
        dst_office_address=item.dst_office_address,
        order_dt=item.order_dt.isoformat() if item.order_dt else None,
        status_changed_at=view.status_changed_at.isoformat(),
        ready_at=_iso(view.ready_at),
        free_until=_iso(view.free_until),
        pickup_deadline=_iso(view.pickup_deadline),
    )


def claim_response(view: ClaimView) -> ClaimResponse:
    claim = view.claim
    return ClaimResponse(
        id=claim.id,
        nm_id=claim.nm_id,
        name=claim.imt_name,
        user_comment=claim.user_comment,
        wb_comment=claim.wb_comment,
        status=claim.status,
        status_ex=claim.status_ex,
        is_archive=claim.is_archive,
        price=claim.price,
        currency_code=claim.currency_code,
        srid=claim.srid,
        photos=list(claim.photo_urls),
        videos=[f"https:{url}" if url.startswith("//") else url for url in claim.video_paths],
        actions=list(claim.actions),
        created_at=claim.dt.isoformat(),
        order_dt=_iso(claim.order_dt),
        delivery_dt=_iso(claim.delivery_dt),
        review_deadline=view.review_deadline.isoformat(),
    )


def returns_response(view: ReturnsView) -> ReturnsResponse:
    return ReturnsResponse(
        seller_id=str(view.seller_id),
        seller_name=view.seller_name,
        collected_at=_iso(view.collected_at),
        collection_error=view.collection_error,
        ready=[return_response(item) for item in view.ready],
        transit=[return_response(item) for item in view.transit],
        other_active=[return_response(item) for item in view.other_active],
        history=[return_response(item) for item in view.history],
        claims=[claim_response(item) for item in view.claims],
        claims_history=[claim_response(item) for item in view.claims_history],
    )


def refresh_response(state: RefreshRequest) -> RefreshResponse:
    return RefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=_iso(state.finished_at),
        error=state.error,
    )


@router.get("/sellers/{seller_id}", response_model=ReturnsResponse)
@inject
async def seller_returns(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ReturnsService]
) -> ReturnsResponse:
    try:
        view = await service.view(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return returns_response(view)


@router.post("/sellers/{seller_id}/invite-link", response_model=InviteLinkResponse)
@inject
async def invite_link(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[ReturnsService]
) -> InviteLinkResponse:
    """Свежая персональная ссылка на бота. Выпуск новой гасит прежнюю."""
    try:
        invite = await service.invite_link(seller_id, created_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except NotificationBotMissingError as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Бот возвратов ещё не зарегистрирован — добавьте его в админке на странице «Боты»",
        ) from error
    return InviteLinkResponse(url=invite.url, expires_at=invite.expires_at.isoformat())


@router.post("/sellers/{seller_id}/refresh", response_model=RefreshResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[ReturnsService]
) -> RefreshResponse:
    """Собрать возвраты и заявки вне расписания. Повторное нажатие возвращает тот же запрос."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=RefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ReturnsService]
) -> RefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state) if state else None


def extension_response(view: ExtensionView) -> ExtensionResponse:
    return ExtensionResponse(
        seller_id=str(view.seller_id),
        download_url=view.download_url,
        code_date=view.code_date.isoformat(),
        has_code_today=view.has_code_today,
        code_received_at=_iso(view.code_received_at),
        pending_tasks=view.pending_tasks,
        installs=[
            ExtensionInstallResponse(
                id=str(item.id),
                install_id=item.install_id,
                browser=item.browser,
                created_at=item.created_at.isoformat(),
                last_seen_at=_iso(item.last_seen_at),
                state=item.state,
                last_error=item.last_error,
                last_code_at=_iso(item.last_code_at),
                deliveries_count=item.deliveries_count,
                deliveries_at=_iso(item.deliveries_at),
            )
            for item in view.installs
        ],
    )


@router.get("/sellers/{seller_id}/extension", response_model=ExtensionResponse)
@inject
async def extension_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ExtensionService]
) -> ExtensionResponse:
    try:
        view = await service.view(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return extension_response(view)


@router.post("/sellers/{seller_id}/extension/pairing-code", response_model=PairingCodeResponse)
@inject
async def pairing_code(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[ExtensionService]
) -> PairingCodeResponse:
    """Код пары со страницы — для менеджера, который ставит расширение без бота."""
    try:
        pairing = await service.create_pairing_code(seller_id, created_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    await service.session.commit()
    return PairingCodeResponse(code=pairing.code, expires_at=pairing.expires_at.isoformat())


@router.delete("/sellers/{seller_id}/extension/installs/{install_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def revoke_install(
    seller_id: uuid.UUID, install_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[ExtensionService]
) -> Response:
    try:
        revoked = await service.revoke(seller_id, install_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    if not revoked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Установка не найдена или уже отключена")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
