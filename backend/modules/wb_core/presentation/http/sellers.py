import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import (
    DuplicateCredentialError,
    SellerArchivedError,
    SellerNotFoundError,
    SellerService,
)
from backend.modules.wb_core.presentation.http.schemas import (
    ArticleResponse,
    SellerCreate,
    SellerResponse,
    SellerRestore,
    SellerUpdate,
)
from backend.modules.wb_core.presentation.http.utils import (
    archived_conflict,
    article_response,
    not_found,
    one_seller_response,
    seller_response,
    seller_responses,
)

router = APIRouter(prefix="/wb/sellers", tags=["wb-sellers"])


@router.get("", response_model=list[SellerResponse])
@inject
async def list_sellers(
    _: CurrentPrincipal,
    service: FromDishka[SellerService],
    include_archived: bool = Query(default=False),
) -> list[SellerResponse]:
    return await seller_responses(service, await service.list_sellers(include_archived=include_archived))


@router.post("", response_model=SellerResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_seller(
    payload: SellerCreate, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> SellerResponse:
    try:
        seller = await service.create(payload.name, payload.api_key.get_secret_value())
    except DuplicateCredentialError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот API-ключ уже используется") from error
    return seller_response(seller)


@router.patch("/{seller_id}", response_model=SellerResponse)
@inject
async def update_seller(
    seller_id: uuid.UUID, payload: SellerUpdate, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> SellerResponse:
    try:
        seller = await service.update(
            seller_id, payload.name, payload.api_key.get_secret_value() if payload.api_key else None
        )
    except SellerNotFoundError as error:
        raise not_found() from error
    except SellerArchivedError as error:
        raise archived_conflict() from error
    except DuplicateCredentialError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот API-ключ уже используется") from error
    return await one_seller_response(service, seller)


@router.delete("/{seller_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_seller(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[SellerService],
    purge: bool = Query(default=False),
) -> Response:
    """Archive the seller, or erase him and all collected history when purge=true."""
    try:
        if purge:
            await service.purge(seller_id)
        else:
            await service.archive(seller_id)
    except SellerNotFoundError as error:
        raise not_found() from error
    except SellerArchivedError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{seller_id}/restore", response_model=SellerResponse)
@inject
async def restore_seller(
    seller_id: uuid.UUID, payload: SellerRestore, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> SellerResponse:
    try:
        seller = await service.restore(seller_id, payload.api_key.get_secret_value())
    except SellerNotFoundError as error:
        raise not_found() from error
    except DuplicateCredentialError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот API-ключ уже используется") from error
    return await one_seller_response(service, seller)


@router.post("/{seller_id}/egress-verify", response_model=SellerResponse)
@inject
async def egress_verify(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> SellerResponse:
    """Повторная проверка ключа на шлюзе: для key_invalid после починки прав в кабинете WB."""
    try:
        seller = await service.refresh_egress(seller_id)
    except SellerNotFoundError as error:
        raise not_found() from error
    return await one_seller_response(service, seller)


@router.post("/{seller_id}/catalog-sync", response_model=SellerResponse)
@inject
async def retry_sync(seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]) -> SellerResponse:
    try:
        seller = await service.request_sync(seller_id)
    except SellerNotFoundError as error:
        raise not_found() from error
    except SellerArchivedError as error:
        raise archived_conflict() from error
    return await one_seller_response(service, seller)


@router.get("/{seller_id}/articles", response_model=list[ArticleResponse])
@inject
async def list_articles(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]
) -> list[ArticleResponse]:
    try:
        articles = await service.articles(seller_id)
    except SellerNotFoundError as error:
        raise not_found() from error
    return [article_response(item) for item in articles]
