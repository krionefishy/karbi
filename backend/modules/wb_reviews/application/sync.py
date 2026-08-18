import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.domain import ReviewSyncRun
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.shared.kafka_streams.topics import WBReviewsTopics
from backend.shared.outbox import OutboxRepository


@dataclass(frozen=True, slots=True)
class ProductHistory:
    id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    snapshots: list[dict]


@dataclass(frozen=True, slots=True)
class ReviewHistory:
    seller_id: uuid.UUID
    products: list[ProductHistory]


class ReviewSyncService:
    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        reviews: ReviewSyncRepository,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.reviews = reviews

    async def request(self, trigger: str, snapshot_date: date) -> ReviewSyncRun:
        await self.reviews.lock_run_creation()
        active = await self.reviews.active_run()
        if active is not None:
            return self.reviews.to_domain(active)
        if trigger == "scheduled":
            existing = await self.reviews.run_for_date(snapshot_date)
            if existing is not None:
                return self.reviews.to_domain(existing)
        seller_ids = [seller.id for seller in await self.sellers.list_sellers()]
        run, jobs = await self.reviews.create_run(trigger, snapshot_date, seller_ids)
        outbox = OutboxRepository(self.session)
        for job in jobs:
            outbox.add(
                aggregate_id=job.seller_id,
                event_type="WBReviewSyncRequested",
                topic=WBReviewsTopics.SYNC_REQUESTED,
                payload={
                    "run_id": str(run.id),
                    "job_id": str(job.id),
                    "seller_id": str(job.seller_id),
                    "snapshot_date": snapshot_date.isoformat(),
                    "schema_version": 1,
                },
            )
        await self.session.commit()
        return self.reviews.to_domain(run)

    async def latest(self) -> ReviewSyncRun | None:
        return await self.reviews.latest_run()

    async def history(self, seller_id: uuid.UUID, days: int) -> ReviewHistory:
        if await self.sellers.get(seller_id) is None:
            raise SellerNotFoundError
        articles = await self.sellers.list_articles(seller_id)
        counts = await self.reviews.history(seller_id, days)
        by_article: dict[str, list[dict]] = {}
        for count in counts:
            by_article.setdefault(count.article, []).append(
                {
                    "date": count.date.isoformat(),
                    "ratings": {index + 1: value for index, value in enumerate(count.ratings)},
                }
            )
        return ReviewHistory(
            seller_id,
            [
                ProductHistory(
                    article.id,
                    article.article,
                    article.vendor_code,
                    article.name,
                    by_article.get(article.article, []),
                )
                for article in articles
            ],
        )
