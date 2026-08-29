from collections.abc import Sequence

from fastapi import HTTPException, status

from backend.modules.wb_core.application import SellerService
from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.presentation.http.schemas import ArticleResponse, SellerResponse


def seller_response(seller: Seller, automations: Sequence[str] = ()) -> SellerResponse:
    return SellerResponse(
        id=seller.id,
        name=seller.name,
        product_count=seller.product_count,
        catalog_sync_status=seller.catalog_sync_status,
        last_catalog_sync_at=seller.last_catalog_sync_at.isoformat() if seller.last_catalog_sync_at else None,
        catalog_sync_error=seller.catalog_sync_error,
        archived_at=seller.archived_at.isoformat() if seller.archived_at else None,
        automations=list(automations),
        egress_status=seller.egress_status,
        egress_error=seller.egress_error,
        egress_ip=seller.egress_ip,
    )


async def seller_responses(service: SellerService, sellers: Sequence[Seller]) -> list[SellerResponse]:
    """Render sellers together with the automations they are connected to."""
    membership = await service.automations_of([seller.id for seller in sellers])
    return [seller_response(seller, membership.get(seller.id, [])) for seller in sellers]


async def one_seller_response(service: SellerService, seller: Seller) -> SellerResponse:
    return (await seller_responses(service, [seller]))[0]


def article_response(article: Article) -> ArticleResponse:
    return ArticleResponse(
        id=article.id,
        seller_id=article.seller_id,
        article=article.article,
        vendor_code=article.vendor_code,
        name=article.name,
        imt_id=article.imt_id,
        brand=article.brand,
        subject_name=article.subject_name,
        photo_url=article.photo_url,
        state=article.state,
    )


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не найден")


def archived_conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Селлер в архиве — сначала восстановите его")
