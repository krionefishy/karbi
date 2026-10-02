from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from backend.modules.wb_core.application import SellerService
from backend.modules.wb_core.domain import Article, Seller, TaxRate, tax_rate_on
from backend.modules.wb_core.presentation.http.schemas import ArticleResponse, SellerResponse

# Какая ставка «действует сегодня», решает московская дата — как у отчётов WB.
MOSCOW = ZoneInfo("Europe/Moscow")


def seller_response(seller: Seller, automations: Sequence[str] = (), tax: TaxRate | None = None) -> SellerResponse:
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
        ozon_egress_status=seller.ozon_egress_status,
        ozon_egress_error=seller.ozon_egress_error,
        mpstats_egress_status=seller.mpstats_egress_status,
        mpstats_egress_error=seller.mpstats_egress_error,
        egress_ip=seller.egress_ip,
        tax_rate=float(tax.rate) if tax else None,
        tax_rate_from=tax.effective_from.isoformat() if tax else None,
    )


async def seller_responses(service: SellerService, sellers: Sequence[Seller]) -> list[SellerResponse]:
    """Render sellers together with the automations they are connected to."""
    ids = [seller.id for seller in sellers]
    membership = await service.automations_of(ids)
    rates = await service.tax_rates(ids)
    today = datetime.now(MOSCOW).date()
    return [
        seller_response(seller, membership.get(seller.id, []), tax_rate_on(rates.get(seller.id, []), today))
        for seller in sellers
    ]


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
