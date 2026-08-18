import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import DuplicateCredentialError, SellerNotFoundError, SellerService
from backend.modules.wb_core.domain import Seller

router = APIRouter(prefix="/wb/sellers", tags=["wb-sellers"])


class SellerCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    api_key: SecretStr = Field(min_length=10, max_length=4096)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("Seller name is too short")
        return normalized

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr) -> SecretStr:
        normalized = value.get_secret_value().strip()
        if len(normalized) < 10:
            raise ValueError("API key is too short")
        return SecretStr(normalized)


class SellerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    api_key: SecretStr | None = Field(default=None, min_length=10, max_length=4096)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return SellerCreate.normalize_name(value) if value is not None else None

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        return SellerCreate.normalize_api_key(value) if value is not None else None

    @model_validator(mode="after")
    def at_least_one_field(self):
        if self.name is None and self.api_key is None:
            raise ValueError("At least one field is required")
        return self


class SellerResponse(BaseModel):
    id: uuid.UUID
    name: str
    product_count: int
    catalog_sync_status: str
    last_catalog_sync_at: str | None
    catalog_sync_error: str | None


class ArticleResponse(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None
    brand: str
    subject_name: str
    photo_url: str
    state: str


def seller_response(seller: Seller) -> SellerResponse:
    return SellerResponse(
        id=seller.id,
        name=seller.name,
        product_count=seller.product_count,
        catalog_sync_status=seller.catalog_sync_status,
        last_catalog_sync_at=seller.last_catalog_sync_at.isoformat() if seller.last_catalog_sync_at else None,
        catalog_sync_error=seller.catalog_sync_error,
    )


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден")


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
    return [
        ArticleResponse(
            id=item.id,
            seller_id=item.seller_id,
            article=item.article,
            vendor_code=item.vendor_code,
            name=item.name,
            imt_id=item.imt_id,
            brand=item.brand,
            subject_name=item.subject_name,
            photo_url=item.photo_url,
            state=item.state,
        )
        for item in articles
    ]
