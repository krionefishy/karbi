from fastapi import HTTPException, status

from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.presentation.http.schemas import ArticleResponse, SellerResponse


def seller_response(seller: Seller) -> SellerResponse:
    return SellerResponse(
        id=seller.id,
        name=seller.name,
        product_count=seller.product_count,
        catalog_sync_status=seller.catalog_sync_status,
        last_catalog_sync_at=seller.last_catalog_sync_at.isoformat() if seller.last_catalog_sync_at else None,
        catalog_sync_error=seller.catalog_sync_error,
    )


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
