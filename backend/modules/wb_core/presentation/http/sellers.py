import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import DuplicateCredentialError, SellerNotFoundError, SellerService
from backend.modules.wb_core.presentation.http.schemas import (
    ArticleResponse,
    SellerCreate,
    SellerResponse,
    SellerUpdate,
)
from backend.modules.wb_core.presentation.http.utils import article_response, not_found, seller_response

router = APIRouter(prefix="/wb/sellers", tags=["wb-sellers"])


@router.get("", response_model=list[SellerResponse])
@inject
async def list_sellers(_: CurrentPrincipal, service: FromDishka[SellerService]) -> list[SellerResponse]:
    return [seller_response(item) for item in await service.list_sellers()]


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
    except DuplicateCredentialError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот API-ключ уже используется") from error
    return seller_response(seller)


@router.delete("/{seller_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_seller(seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]) -> Response:
    try:
        await service.delete(seller_id)
    except SellerNotFoundError as error:
        raise not_found() from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{seller_id}/catalog-sync", response_model=SellerResponse)
@inject
async def retry_sync(seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[SellerService]) -> SellerResponse:
    try:
        return seller_response(await service.request_sync(seller_id))
    except SellerNotFoundError as error:
        raise not_found() from error


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
