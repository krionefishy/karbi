import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_core.infrastructure.wb.egress import EgressGateway

FEEDBACKS_BUCKET = "feedbacks"
# WB stops serving feedback pages somewhere around this offset. Failing loudly
# is deliberate: a snapshot silently cut off at the ceiling would be written to
# the database as if it were complete.
MAX_PAGINATION_DEPTH = 200_000


# Исторические имена модуля отзывов; наполнение теперь общее для всех клиентов.
WBFeedbackPermanentError = WBPermanentError
WBFeedbackTemporaryError = WBTemporaryError


@dataclass(frozen=True, slots=True)
class FeedbackProduct:
    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None


@dataclass(frozen=True, slots=True)
class FeedbackAggregation:
    counts: dict[str, tuple[int, int, int, int, int]]
    products: dict[str, FeedbackProduct]
    feedback_count: int


class WBFeedbackClient:
    """Отзывы через шлюз wb-egress: паузы и ретраи — забота шлюза."""

    def __init__(self, gateway: EgressGateway, page_size: int = 5000) -> None:
        if not 1 <= page_size <= 5000:
            raise ValueError("WB feedback page size must be between 1 and 5000")
        self.gateway = gateway
        self.page_size = page_size
        self.logger = logging.getLogger("wb.feedbacks.client")

    async def aggregate(self, seller_id: str) -> FeedbackAggregation:
        """Every feedback of the account, counted per nmID.

        Three passes are needed: unanswered, answered and the archive. A
        feedback that has been answered but not yet archived belongs to none of
        the other two buckets, so dropping the middle pass silently undercounts.
        Ids seen twice across passes are deduplicated.
        """
        counts: dict[str, list[int]] = {}
        products: dict[str, FeedbackProduct] = {}
        seen: set[bytes] = set()
        for path, extra_params in (
            ("/api/v1/feedbacks", {"isAnswered": "false"}),
            ("/api/v1/feedbacks", {"isAnswered": "true"}),
            ("/api/v1/feedbacks/archive", {}),
        ):
            await self._consume_pages(seller_id, path, extra_params, counts, products, seen)
        return FeedbackAggregation(
            counts={
                article: (values[0], values[1], values[2], values[3], values[4]) for article, values in counts.items()
            },
            products=products,
            feedback_count=len(seen),
        )

    async def _consume_pages(
        self,
        seller_id: str,
        path: str,
        extra_params: dict[str, str],
        counts: dict[str, list[int]],
        products: dict[str, FeedbackProduct],
        seen: set[bytes],
    ) -> None:
        skip = 0
        while True:
            if skip >= MAX_PAGINATION_DEPTH:
                raise WBFeedbackPermanentError(
                    f"WB Feedbacks API: пагинация {path} упёрлась в потолок {MAX_PAGINATION_DEPTH} отзывов, "
                    "снапшот был бы усечён"
                )
            payload = (
                await self.gateway.call(
                    seller_id=seller_id,
                    api=FEEDBACKS_BUCKET,
                    method="GET",
                    path=path,
                    params={**extra_params, "take": str(self.page_size), "skip": str(skip), "order": "dateAsc"},
                    api_name="WB Feedbacks API",
                    category="Вопросы и отзывы",
                )
                or {}
            )
            if payload.get("error"):
                raise WBFeedbackPermanentError(str(payload.get("errorText") or "WB rejected feedback request"))
            data = payload.get("data") or {}
            if not isinstance(data, dict):
                raise WBFeedbackPermanentError("WB returned an invalid feedback payload")
            feedbacks = data.get("feedbacks", [])
            if not isinstance(feedbacks, list):
                raise WBFeedbackPermanentError("WB returned an invalid feedback list")
            for feedback in feedbacks:
                self._accumulate(feedback, counts, products, seen)
            page_length = len(feedbacks)
            del feedbacks, payload
            if page_length < self.page_size:
                return
            skip += page_length

    @staticmethod
    def _accumulate(
        feedback: dict[str, Any],
        counts: dict[str, list[int]],
        products: dict[str, FeedbackProduct],
        seen: set[bytes],
    ) -> None:
        feedback_id = feedback.get("id")
        details = feedback.get("productDetails") or {}
        article_value = details.get("nmId")
        rating_value = feedback.get("productValuation")
        if not feedback_id or article_value is None or not isinstance(rating_value, int) or not 1 <= rating_value <= 5:
            return
        digest = hashlib.blake2b(str(feedback_id).encode(), digest_size=16).digest()
        if digest in seen:
            return
        seen.add(digest)
        article = str(article_value)
        counts.setdefault(article, [0, 0, 0, 0, 0])[rating_value - 1] += 1
        imt_value = str(details.get("imtId") or "")
        products.setdefault(
            article,
            FeedbackProduct(
                article=article,
                vendor_code=str(details.get("supplierArticle") or ""),
                name=str(details.get("productName") or details.get("supplierArticle") or article),
                imt_id=int(imt_value) if imt_value.isdigit() else None,
            ),
        )
