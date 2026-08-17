import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_reviews.domain import DailyRatings, ReviewSyncRun
from backend.modules.wb_reviews.infrastructure.postgres.models import (
    DailyReviewCountModel,
    ReviewSyncJobModel,
    ReviewSyncRunModel,
)

ACTIVE_RUN_STATUSES = ("queued", "running")
MOSCOW = ZoneInfo("Europe/Moscow")


class ReviewSyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_run_creation(self) -> None:
        """Serialize sync-run creation across API and scheduler processes."""
        await self.session.execute(select(func.pg_advisory_xact_lock(1_947_521_608)))

    async def active_run(self) -> ReviewSyncRunModel | None:
        return await self.session.scalar(
            select(ReviewSyncRunModel)
            .where(ReviewSyncRunModel.status.in_(ACTIVE_RUN_STATUSES))
            .order_by(ReviewSyncRunModel.created_at.desc())
            .limit(1)
        )

    async def run_for_date(self, snapshot_date: date) -> ReviewSyncRunModel | None:
        return await self.session.scalar(
            select(ReviewSyncRunModel)
            .where(ReviewSyncRunModel.snapshot_date == snapshot_date)
            .order_by(ReviewSyncRunModel.created_at.desc())
            .limit(1)
        )

    async def latest_run(self) -> ReviewSyncRun | None:
        model = await self.session.scalar(
            select(ReviewSyncRunModel).order_by(ReviewSyncRunModel.created_at.desc()).limit(1)
        )
        return self.to_domain(model) if model else None

    async def create_run(
        self, trigger: str, snapshot_date: date, seller_ids: list[uuid.UUID]
    ) -> tuple[ReviewSyncRunModel, list[ReviewSyncJobModel]]:
        run = ReviewSyncRunModel(
            id=uuid.uuid4(),
            trigger=trigger,
            snapshot_date=snapshot_date,
            status="queued" if seller_ids else "success",
            total_sellers=len(seller_ids),
            finished_at=None if seller_ids else datetime.now(UTC),
        )
        self.session.add(run)
        # These models deliberately have no ORM relationship, so make the FK
        # insertion order explicit while keeping run, jobs and outbox atomic.
        await self.session.flush()
        jobs = [
            ReviewSyncJobModel(id=uuid.uuid4(), run_id=run.id, seller_id=seller_id, status="queued")
            for seller_id in seller_ids
        ]
        self.session.add_all(jobs)
        return run, jobs

    async def jobs_for_run(self, run_id: uuid.UUID) -> list[ReviewSyncJobModel]:
        return list(
            await self.session.scalars(
                select(ReviewSyncJobModel)
                .where(ReviewSyncJobModel.run_id == run_id)
                .order_by(ReviewSyncJobModel.seller_id)
            )
        )

    async def get_job(self, job_id: uuid.UUID) -> ReviewSyncJobModel | None:
        return await self.session.get(ReviewSyncJobModel, job_id)

    async def mark_job_running(self, job_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id, ReviewSyncJobModel.status == "queued")
            .values(status="running", started_at=datetime.now(UTC), error=None)
            .returning(ReviewSyncJobModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_run_running(self, run_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ReviewSyncRunModel)
            .where(ReviewSyncRunModel.id == run_id, ReviewSyncRunModel.status == "queued")
            .values(status="running", started_at=datetime.now(UTC))
        )

    async def complete_job(self, job_id: uuid.UUID, product_count: int, feedback_count: int) -> None:
        await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id)
            .values(
                status="success",
                product_count=product_count,
                feedback_count=feedback_count,
                error=None,
                finished_at=datetime.now(UTC),
            )
        )

    async def fail_job(self, job_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id)
            .values(status="error", error=error[:1000], finished_at=datetime.now(UTC))
        )

    async def finalize_run(self, run_id: uuid.UUID) -> None:
        counts = await self.session.execute(
            select(ReviewSyncJobModel.status, func.count())
            .where(ReviewSyncJobModel.run_id == run_id)
            .group_by(ReviewSyncJobModel.status)
        )
        grouped = {status: int(count) for status, count in counts.all()}
        success = grouped.get("success", 0)
        failed = grouped.get("error", 0)
        pending = grouped.get("queued", 0) + grouped.get("running", 0)
        values: dict = {"completed_sellers": success, "failed_sellers": failed}
        if pending == 0:
            values["finished_at"] = datetime.now(UTC)
            values["status"] = "success" if not failed else "error" if not success else "partial_success"
        await self.session.execute(update(ReviewSyncRunModel).where(ReviewSyncRunModel.id == run_id).values(**values))

    async def upsert_daily_counts(
        self,
        seller_id: uuid.UUID,
        snapshot_date: date,
        article_counts: dict[str, tuple[int, int, int, int, int]],
    ) -> None:
        now = datetime.now(UTC)
        rows = [
            {
                "seller_id": seller_id,
                "article": article,
                "date": snapshot_date,
                "count_rating_1": ratings[0],
                "count_rating_2": ratings[1],
                "count_rating_3": ratings[2],
                "count_rating_4": ratings[3],
                "count_rating_5": ratings[4],
                "collected_at": now,
            }
            for article, ratings in article_counts.items()
        ]
        for offset in range(0, len(rows), 1000):
            statement = insert(DailyReviewCountModel).values(rows[offset : offset + 1000])
            statement = statement.on_conflict_do_update(
                index_elements=["seller_id", "article", "date"],
                set_={
                    "count_rating_1": statement.excluded.count_rating_1,
                    "count_rating_2": statement.excluded.count_rating_2,
                    "count_rating_3": statement.excluded.count_rating_3,
                    "count_rating_4": statement.excluded.count_rating_4,
                    "count_rating_5": statement.excluded.count_rating_5,
                    "collected_at": statement.excluded.collected_at,
                },
            )
            await self.session.execute(statement)

    async def history(self, seller_id: uuid.UUID, days: int) -> list[DailyRatings]:
        since = datetime.now(MOSCOW).date() - timedelta(days=days - 1)
        rows = await self.session.scalars(
            select(DailyReviewCountModel)
            .where(DailyReviewCountModel.seller_id == seller_id, DailyReviewCountModel.date >= since)
            .order_by(DailyReviewCountModel.article, DailyReviewCountModel.date)
        )
        return [
            DailyRatings(
                row.seller_id,
                row.article,
                row.date,
                (
                    row.count_rating_1,
                    row.count_rating_2,
                    row.count_rating_3,
                    row.count_rating_4,
                    row.count_rating_5,
                ),
                row.collected_at,
            )
            for row in rows
        ]

    @staticmethod
    def to_domain(model: ReviewSyncRunModel) -> ReviewSyncRun:
        return ReviewSyncRun(
            model.id,
            model.trigger,
            model.snapshot_date,
            model.status,
            model.total_sellers,
            model.completed_sellers,
            model.failed_sellers,
            model.created_at,
            model.started_at,
            model.finished_at,
        )
