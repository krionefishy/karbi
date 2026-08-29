import logging
from dataclasses import dataclass, field
from typing import Any

from backend.modules.wb_core.infrastructure.wb.egress import (
    EgressGateway,
    WBPermanentError,
    WBTemporaryError,
)

CONTENT_BUCKET = "content"
PAGE_LIMIT = 100

__all__ = [
    "CONTENT_BUCKET",
    "PAGE_LIMIT",
    "CatalogCard",
    "CatalogSnapshot",
    "WBContentClient",
    "WBPermanentError",
    "WBTemporaryError",
]


@dataclass(frozen=True, slots=True)
class CatalogCard:
    """One WB card. `article` is the nmID; `imt_id` is the склейка it belongs to."""

    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None
    brand: str = ""
    subject_id: int | None = None
    subject_name: str = ""
    photo_url: str = ""
    sizes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    active: list[CatalogCard]
    archived: list[CatalogCard]
    archived_available: bool


class WBContentClient:
    """Каталог селлера через шлюз wb-egress: запрос уходит под seller_id."""

    endpoint = "/content/v2/get/cards/list"
    trash_endpoint = "/content/v2/get/cards/trash"

    def __init__(self, gateway: EgressGateway) -> None:
        self.gateway = gateway
        self.logger = logging.getLogger("wb.content.client")

    async def get_catalog(self, seller_id: str) -> CatalogSnapshot:
        """Both halves of the account: cards in sale and cards the seller archived.

        `cards/list` never returns archived cards, so a товар moved to the корзина
        would otherwise look as if it had vanished from WB entirely.
        """
        active = await self._consume(seller_id, self.endpoint, {"withPhoto": -1})
        try:
            archived = await self._consume(seller_id, self.trash_endpoint, None)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("wb_trash_unavailable", extra={"error": str(error)})
            return CatalogSnapshot(active=active, archived=[], archived_available=False)
        return CatalogSnapshot(active=active, archived=archived, archived_available=True)

    async def get_articles(self, seller_id: str) -> list[CatalogCard]:
        return await self._consume(seller_id, self.endpoint, {"withPhoto": -1})

    async def _consume(
        self,
        seller_id: str,
        endpoint: str,
        card_filter: dict[str, Any] | None,
    ) -> list[CatalogCard]:
        cursor: dict[str, Any] = {"limit": PAGE_LIMIT}
        cards: list[CatalogCard] = []
        while True:
            settings: dict[str, Any] = {"sort": {"ascending": True}, "cursor": cursor}
            if card_filter is not None:
                settings["filter"] = card_filter
            payload = (
                await self.gateway.call(
                    seller_id=seller_id,
                    api=CONTENT_BUCKET,
                    method="POST",
                    path=endpoint,
                    json={"settings": settings},
                    api_name="WB Content API",
                    category="Контент",
                )
                or {}
            )
            page = payload.get("cards") or []
            cards.extend(self._card(item) for item in page if item.get("nmID") is not None)
            result_cursor = payload.get("cursor") or {}
            if int(result_cursor.get("total", len(page))) < PAGE_LIMIT:
                return cards
            # WB names the continuation field differently per endpoint (updatedAt for
            # the catalog, trashedAt for the корзина), so echo back whatever it sent.
            continuation = {key: value for key, value in result_cursor.items() if key != "total"}
            if not continuation:
                raise WBPermanentError("WB вернул некорректный курсор пагинации")
            cursor = {"limit": PAGE_LIMIT, **continuation}

    @staticmethod
    def _card(card: dict[str, Any]) -> CatalogCard:
        vendor_code = str(card.get("vendorCode") or "")
        article = str(card["nmID"])
        return CatalogCard(
            article=article,
            vendor_code=vendor_code,
            name=str(card.get("title") or card.get("subjectName") or vendor_code or article),
            imt_id=int(card["imtID"]) if str(card.get("imtID") or "").isdigit() else None,
            brand=str(card.get("brand") or ""),
            subject_id=int(card["subjectID"]) if str(card.get("subjectID") or "").isdigit() else None,
            subject_name=str(card.get("subjectName") or ""),
            photo_url=WBContentClient._photo(card.get("photos")),
            sizes=WBContentClient._sizes(card.get("sizes")),
        )

    @staticmethod
    def _photo(photos: Any) -> str:
        if not isinstance(photos, list):
            return ""
        for photo in photos:
            if isinstance(photo, str):
                return photo
            if isinstance(photo, dict):
                for size in ("c246x328", "square", "big", "c516x688", "tm"):
                    value = photo.get(size)
                    if isinstance(value, str) and value:
                        return value
        return ""

    @staticmethod
    def _sizes(sizes: Any) -> list[dict[str, Any]]:
        if not isinstance(sizes, list):
            return []
        collected = []
        for size in sizes:
            if not isinstance(size, dict):
                continue
            skus = size.get("skus")
            collected.append(
                {
                    "chrt_id": size.get("chrtID"),
                    "tech_size": str(size.get("techSize") or ""),
                    "skus": [str(sku) for sku in skus] if isinstance(skus, list) else [],
                }
            )
        return collected
