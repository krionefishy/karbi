import uuid
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.domain import ReviewSyncJob, ReviewSyncRun
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.shared.kafka_streams.topics import WBReviewsTopics
from backend.shared.outbox import OutboxRepository

RATINGS = (1, 2, 3, 4, 5)


@dataclass(frozen=True, slots=True)
class ProductHistory:
    id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None
    brand: str
    photo_url: str
    state: str
    snapshots: list[dict]
    # Same dates, but summed across every article of the склейка — this is the
    # number a seller sees on the WB card itself.
    card_snapshots: list[dict]


@dataclass(frozen=True, slots=True)
class ReviewHistory:
    seller_id: uuid.UUID
    products: list[ProductHistory]


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    expired_leases: int
    requeued_jobs: int
    abandoned_runs: int

    @property
    def changed(self) -> bool:
        return bool(self.expired_leases or self.requeued_jobs or self.abandoned_runs)


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
        for job in jobs:
            self._publish_job(run.id, job.id, job.seller_id, snapshot_date)
        await self.session.commit()
        return self.reviews.to_domain(run)

    async def run_maintenance(self, *, max_attempts: int, max_run_age_seconds: int) -> MaintenanceReport:
        """Recover jobs nobody will finish and re-dispatch the ones due for a retry."""
        await self.reviews.lock_run_creation()
        expired = await self.reviews.expire_leases(max_attempts)
        requeued = 0
        for job in await self.reviews.claim_due_jobs():
            run = await self.reviews.run_of_job(job.id)
            if run is None:
                continue
            self._publish_job(run.id, job.id, job.seller_id, run.snapshot_date)
            requeued += 1
        abandoned = await self.reviews.abandon_stale_runs(max_run_age_seconds)
        await self.session.commit()
        return MaintenanceReport(expired, requeued, len(abandoned))

    async def latest(self) -> ReviewSyncRun | None:
        run = await self.reviews.latest_run()
        if run is None:
            return None
        jobs = []
        for job in await self.reviews.jobs_for_run(run.id):
            seller = await self.sellers.get(job.seller_id)
            jobs.append(
                ReviewSyncJob(
                    id=job.id,
                    seller_id=job.seller_id,
                    seller_name=seller.name if seller else "Удалённый селлер",
                    status=job.status,
                    product_count=job.product_count,
                    feedback_count=job.feedback_count,
                    error=job.error,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                    attempts=job.attempts,
                )
            )
        return replace(run, jobs=tuple(jobs))

    async def history(self, seller_id: uuid.UUID, days: int) -> ReviewHistory:
        if await self.sellers.get(seller_id) is None:
            raise SellerNotFoundError
        articles = await self.sellers.list_articles(seller_id)
        by_article: dict[str, dict[str, list[int]]] = {}
        for count in await self.reviews.history(seller_id, days):
            by_article.setdefault(count.article, {})[count.date.isoformat()] = list(count.ratings)
        card_totals = self._card_totals(articles, by_article)
        return ReviewHistory(
            seller_id,
            [
                ProductHistory(
                    id=article.id,
                    article=article.article,
                    vendor_code=article.vendor_code,
                    name=article.name,
                    imt_id=article.imt_id,
                    brand=article.brand,
                    photo_url=article.photo_url,
                    state=article.state,
                    snapshots=self._snapshots(by_article.get(article.article, {})),
                    card_snapshots=self._snapshots(card_totals[self._card_key(article)]),
                )
                for article in articles
            ],
        )

    @staticmethod
    def _card_key(article: Article) -> str:
        """Group by склейка, falling back to the article itself when WB gave no imtID."""
        return f"imt:{article.imt_id}" if article.imt_id is not None else f"nm:{article.article}"

    @classmethod
    def _card_totals(
        cls, articles: list[Article], by_article: dict[str, dict[str, list[int]]]
    ) -> dict[str, dict[str, list[int]]]:
        totals: dict[str, dict[str, list[int]]] = {}
        for article in articles:
            card = totals.setdefault(cls._card_key(article), {})
            for day, ratings in by_article.get(article.article, {}).items():
                running = card.setdefault(day, [0, 0, 0, 0, 0])
                for index, value in enumerate(ratings):
                    running[index] += value
        return totals

    @staticmethod
    def _snapshots(by_day: dict[str, list[int]]) -> list[dict]:
        return [{"date": day, "ratings": dict(zip(RATINGS, by_day[day], strict=True))} for day in sorted(by_day)]

    def _publish_job(self, run_id: uuid.UUID, job_id: uuid.UUID, seller_id: uuid.UUID, snapshot_date: date) -> None:
        OutboxRepository(self.session).add(
            aggregate_id=seller_id,
            event_type="WBReviewSyncRequested",
            topic=WBReviewsTopics.SYNC_REQUESTED,
            payload={
                "run_id": str(run_id),
                "job_id": str(job_id),
                "seller_id": str(seller_id),
                "snapshot_date": snapshot_date.isoformat(),
                "schema_version": 1,
            },
        )
