import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ReviewMirror, SellerNotFoundError
from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.application.groups import (
    ROLE_MAIN,
    GroupCandidate,
    GroupRole,
    Placement,
    card_key,
    group_keys,
    resolve_groups,
)
from backend.modules.wb_reviews.domain import (
    GROUP_MAIN,
    GROUP_MEMBER,
    GROUP_SOLO,
    GroupOverride,
    ReviewSyncJob,
    ReviewSyncRun,
)
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.shared.kafka_streams.topics import WBReviewsTopics
from backend.shared.outbox import OutboxRepository

RATINGS = (1, 2, 3, 4, 5)
GROUP_ACTIONS = ("pin_main", "attach", "detach", "reset")
RatingSource = Literal["wb", "group", "reviews"]
RATING_WB: RatingSource = "wb"
RATING_GROUP: RatingSource = "group"
RATING_REVIEWS: RatingSource = "reviews"


class ProductNotFoundError(Exception):
    """Артикула нет в каталоге селлера."""


class GroupActionError(Exception):
    """Правка группы не имеет смысла: нет цели привязки, привязка к себе, неизвестное действие."""


@dataclass(frozen=True, slots=True)
class ProductHistory:
    id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None
    brand: str
    subject_id: int | None
    subject_name: str
    photo_url: str
    state: str
    snapshots: list[dict]
    card_snapshots: list[dict]
    photo_count: int | None = None
    rating: float | None = None
    rating_source: RatingSource | None = None
    group_id: str | None = None
    group_role: GroupRole | None = None
    group_manual: bool = False
    group_snapshots: list[dict] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ReviewHistory:
    seller_id: uuid.UUID
    products: list[ProductHistory]


SUCCESSFUL_STATUSES = ("success", "partial_success")


@dataclass(frozen=True, slots=True)
class SyncOverview:
    """What the automation card needs: is it alive, and when did it last work."""

    seller_count: int
    last_run: ReviewSyncRun | None
    last_success_at: datetime | None
    runs_last_24h: int

    @property
    def status(self) -> str:
        if self.last_run is None:
            return "idle"
        if self.last_run.status in ("queued", "running"):
            return "running"
        if self.last_run.status == "success":
            return "active"
        if self.last_run.status == "partial_success":
            return "degraded"
        return "failed"


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
        review_mirror: ReviewMirror | None = None,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.reviews = reviews
        self.review_mirror = review_mirror or ReviewMirror(session)

    async def request(self, trigger: str, snapshot_date: date) -> ReviewSyncRun:
        await self.reviews.lock_run_creation()
        active = await self.reviews.active_run()
        if active is not None:
            return self.reviews.to_domain(active)
        if trigger == "scheduled":
            existing = await self.reviews.run_for_date(snapshot_date)
            if existing is not None:
                return self.reviews.to_domain(existing)
        seller_ids = await self.enrolled_seller_ids()
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

    async def enrolled_seller_ids(self) -> list[uuid.UUID]:
        """Sellers connected to this automation, archived ones left out.

        Archiving detaches the seller, so the intersection normally changes
        nothing — but it keeps a stale membership row from sending a job for a
        seller whose API key we no longer hold.
        """
        tracked = await self.reviews.tracked_seller_ids()
        return [seller.id for seller in await self.sellers.list_sellers() if seller.id in tracked]

    async def overview(self) -> SyncOverview:
        """Real state of the automation, assembled from the runs we recorded."""
        return SyncOverview(
            seller_count=len(await self.enrolled_seller_ids()),
            last_run=await self.latest(),
            last_success_at=await self.reviews.last_run_finished_at(SUCCESSFUL_STATUSES),
            runs_last_24h=await self.reviews.count_runs_since(datetime.now(UTC) - timedelta(hours=24)),
        )

    async def history(self, seller_id: uuid.UUID, days: int) -> ReviewHistory:
        if await self.sellers.get(seller_id) is None:
            raise SellerNotFoundError
        articles = await self.sellers.list_articles(seller_id)
        by_article: dict[str, dict[str, list[int]]] = {}
        for count in await self.reviews.history(seller_id, days):
            by_article.setdefault(count.article, {})[count.date.isoformat()] = list(count.ratings)
        card_totals = self._totals(articles, by_article, lambda article: card_key(article.article, article.imt_id))
        placements = resolve_groups(
            self._candidates(articles, by_article), await self.reviews.group_overrides(seller_id)
        )
        group_totals = self._totals(
            articles, by_article, lambda article: placements[article.article].group_id or f"nm:{article.article}"
        )
        facts = await self.review_mirror.totals(seller_id) or {}
        wb_ratings = {article: float(fact.wb_rating) for article, fact in facts.items() if fact.wb_rating is not None}
        ratings = self._ratings(articles, placements, wb_ratings, card_totals)
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
                    subject_id=article.subject_id,
                    subject_name=article.subject_name,
                    photo_url=article.photo_url,
                    state=article.state,
                    snapshots=self._snapshots(by_article.get(article.article, {})),
                    card_snapshots=self._snapshots(card_totals[card_key(article.article, article.imt_id)]),
                    photo_count=article.photo_count,
                    rating=ratings[article.article][0],
                    rating_source=ratings[article.article][1],
                    group_id=placements[article.article].group_id,
                    group_role=placements[article.article].role,
                    group_manual=placements[article.article].manual,
                    group_snapshots=(
                        self._snapshots(group_totals[placements[article.article].group_id or ""])
                        if placements[article.article].role == ROLE_MAIN
                        else []
                    ),
                )
                for article in articles
            ],
        )

    async def change_group(self, seller_id: uuid.UUID, article: str, action: str, main_article: str | None) -> None:
        """Ручная правка группы болванок.

        `pin_main` снимает закрепление с прежнего основного той же группы, но
        его ручную привязку сохраняет; `reset` возвращает товар к склейке WB.
        """
        if await self.sellers.get(seller_id) is None:
            raise SellerNotFoundError
        if action not in GROUP_ACTIONS:
            raise GroupActionError(f"Неизвестное действие: {action}")
        articles = await self.sellers.list_articles(seller_id)
        known = {item.article for item in articles}
        if article not in known:
            raise ProductNotFoundError
        overrides = await self.reviews.group_overrides(seller_id)
        current = overrides.get(article)
        now = datetime.now(UTC)
        if action == "reset":
            await self.reviews.delete_group_override(seller_id, article)
        elif action == "detach":
            await self.reviews.save_group_override(seller_id, article, GROUP_SOLO, None, now=now)
        elif action == "attach":
            if not main_article:
                raise GroupActionError("Не указан основной товар")
            if main_article == article:
                raise GroupActionError("Товар нельзя привязать к самому себе")
            if main_article not in known:
                raise ProductNotFoundError
            await self.reviews.save_group_override(seller_id, article, GROUP_MEMBER, main_article, now=now)
        else:
            keys = group_keys(self._candidates(articles, {}), overrides)
            for other, override in overrides.items():
                if other != article and override.kind == GROUP_MAIN and keys.get(other) == keys[article]:
                    await self._unpin(seller_id, override, now=now)
            target = current.main_article if current is not None and current.kind != GROUP_SOLO else None
            await self.reviews.save_group_override(seller_id, article, GROUP_MAIN, target, now=now)
        await self.session.commit()

    async def _unpin(self, seller_id: uuid.UUID, override: GroupOverride, *, now: datetime) -> None:
        if override.main_article is None:
            await self.reviews.delete_group_override(seller_id, override.article)
        else:
            await self.reviews.save_group_override(
                seller_id, override.article, GROUP_MEMBER, override.main_article, now=now
            )

    @staticmethod
    def _candidates(articles: list[Article], by_article: dict[str, dict[str, list[int]]]) -> list[GroupCandidate]:
        def latest_total(article: str) -> int:
            days = by_article.get(article)
            return sum(days[max(days)]) if days else 0

        return [
            GroupCandidate(article.article, article.imt_id, article.photo_count, latest_total(article.article))
            for article in articles
        ]

    @staticmethod
    def _ratings(
        articles: list[Article],
        placements: dict[str, Placement],
        wb_ratings: dict[str, float],
        card_totals: dict[str, dict[str, list[int]]],
    ) -> dict[str, tuple[float | None, RatingSource | None]]:
        """Рейтинг витрины WB; у болванки — рейтинг основного или склейки; иначе средняя по нашим отзывам.

        Рейтинг у склейки WB общий, а болванки без показов воронка не отдаёт,
        поэтому их рейтинг берётся у соседей, а не считается заново.
        """
        mains = {
            placement.group_id: article
            for article, placement in placements.items()
            if placement.role == ROLE_MAIN and placement.group_id is not None
        }
        by_card: dict[str, float] = {}
        for article in articles:
            if article.article in wb_ratings:
                by_card.setdefault(card_key(article.article, article.imt_id), wb_ratings[article.article])
        result: dict[str, tuple[float | None, RatingSource | None]] = {}
        for article in articles:
            placement = placements[article.article]
            main = mains.get(placement.group_id) if placement.group_id is not None else None
            card = card_key(article.article, article.imt_id)
            if article.article in wb_ratings:
                result[article.article] = (wb_ratings[article.article], RATING_WB)
            elif main is not None and main in wb_ratings:
                result[article.article] = (wb_ratings[main], RATING_GROUP)
            elif article.imt_id is not None and card in by_card:
                result[article.article] = (by_card[card], RATING_GROUP)
            else:
                days = card_totals.get(card) or {}
                counts = days[max(days)] if days else [0, 0, 0, 0, 0]
                total = sum(counts)
                if total:
                    average = sum(rating * count for rating, count in zip(RATINGS, counts, strict=True)) / total
                    result[article.article] = (round(average, 2), RATING_REVIEWS)
                else:
                    result[article.article] = (None, None)
        return result

    @staticmethod
    def _totals(
        articles: list[Article],
        by_article: dict[str, dict[str, list[int]]],
        key_of: Callable[[Article], str],
    ) -> dict[str, dict[str, list[int]]]:
        totals: dict[str, dict[str, list[int]]] = {}
        for article in articles:
            bucket = totals.setdefault(key_of(article), {})
            for day, ratings in by_article.get(article.article, {}).items():
                running = bucket.setdefault(day, [0, 0, 0, 0, 0])
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
