import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_reviews.domain import DailyRatings, ReviewSyncRun
from backend.modules.wb_reviews.infrastructure.postgres.models import (
    DailyReviewCountModel,
    ReviewSyncJobModel,
    ReviewSyncRunModel,
    TrackedSellerModel,
)

ACTIVE_RUN_STATUSES = ("queued", "running")
MOSCOW = ZoneInfo("Europe/Moscow")


class ReviewSyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def is_tracked(self, seller_id: uuid.UUID) -> bool:
        return (
            await self.session.scalar(
                select(TrackedSellerModel.seller_id).where(TrackedSellerModel.seller_id == seller_id)
            )
            is not None
        )

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        """Drop the collected history. Finished runs keep their jobs on purpose:
        they record what the automation did that night, and the API already
        renders a job whose seller is gone as «Удалённый селлер»."""
        await self.untrack(seller_id)
        await self.session.execute(delete(DailyReviewCountModel).where(DailyReviewCountModel.seller_id == seller_id))

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
        """The run that already covers this date, if any.

        A run that ended in error is deliberately not returned: it collected
        nothing usable, so it must not eat the date and block a rerun.
        """
        return await self.session.scalar(
            select(ReviewSyncRunModel)
            .where(
                ReviewSyncRunModel.snapshot_date == snapshot_date,
                ReviewSyncRunModel.status != "error",
            )
            .order_by(ReviewSyncRunModel.created_at.desc())
            .limit(1)
        )

    async def latest_run(self) -> ReviewSyncRun | None:
        model = await self.session.scalar(
            select(ReviewSyncRunModel).order_by(ReviewSyncRunModel.created_at.desc()).limit(1)
        )
        return self.to_domain(model) if model else None

    async def last_run_finished_at(self, statuses: tuple[str, ...]) -> datetime | None:
        """When a run in one of these states last finished."""
        return await self.session.scalar(
            select(ReviewSyncRunModel.finished_at)
            .where(ReviewSyncRunModel.status.in_(statuses), ReviewSyncRunModel.finished_at.is_not(None))
            .order_by(ReviewSyncRunModel.finished_at.desc())
            .limit(1)
        )

    async def count_runs_since(self, moment: datetime) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(ReviewSyncRunModel).where(ReviewSyncRunModel.created_at >= moment)
            )
            or 0
        )

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

    async def mark_job_running(self, job_id: uuid.UUID, lease_seconds: int) -> bool:
        """Claim the job and hold a lease, so a crashed worker cannot wedge the run."""
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id, ReviewSyncJobModel.status == "queued")
            .values(
                status="running",
                started_at=now,
                error=None,
                attempts=ReviewSyncJobModel.attempts + 1,
                next_attempt_at=None,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
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
        # Only a running job may finish: a job the reaper already closed as
        # error must not be rewritten into a success after the fact.
        await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id, ReviewSyncJobModel.status == "running")
            .values(
                status="success",
                product_count=product_count,
                feedback_count=feedback_count,
                error=None,
                finished_at=datetime.now(UTC),
                next_attempt_at=None,
                lease_expires_at=None,
            )
        )

    async def fail_job(self, job_id: uuid.UUID, error: str) -> None:
        # Same guard as complete_job: a job already closed keeps its verdict.
        await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id, ReviewSyncJobModel.status.in_(("queued", "running")))
            .values(
                status="error",
                error=error[:1000],
                finished_at=datetime.now(UTC),
                next_attempt_at=None,
                lease_expires_at=None,
            )
        )

    async def reschedule_job(
        self,
        job_id: uuid.UUID,
        error: str,
        *,
        max_attempts: int,
        backoff_seconds: int,
    ) -> bool:
        """Put a temporarily failed job back in the queue; return False once attempts run out.

        Wildberries answers 429 for minutes at a time, so a spent retry budget
        inside one message must not cost the day's snapshot.
        """
        job = await self.session.get(ReviewSyncJobModel, job_id)
        if job is None:
            return False
        if job.attempts >= max_attempts:
            await self.fail_job(job_id, error)
            return False
        delay = backoff_seconds * (2 ** max(job.attempts - 1, 0))
        await self.session.execute(
            update(ReviewSyncJobModel)
            .where(ReviewSyncJobModel.id == job_id)
            .values(
                status="queued",
                error=error[:1000],
                started_at=None,
                finished_at=None,
                lease_expires_at=None,
                next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay),
            )
        )
        return True

    async def expire_leases(self, max_attempts: int) -> int:
        """Return jobs whose worker died back to the queue for immediate retry."""
        now = datetime.now(UTC)
        expired = list(
            await self.session.scalars(
                select(ReviewSyncJobModel).where(
                    ReviewSyncJobModel.status == "running",
                    ReviewSyncJobModel.lease_expires_at.is_not(None),
                    ReviewSyncJobModel.lease_expires_at < now,
                )
            )
        )
        for job in expired:
            if job.attempts >= max_attempts:
                job.status = "error"
                job.error = "Синхронизация не завершилась за отведённое время"
                job.finished_at = now
            else:
                job.status = "queued"
                job.error = "Воркер не завершил задачу, поставлена в очередь заново"
                job.started_at = None
                job.next_attempt_at = now
            job.lease_expires_at = None
        return len(expired)

    async def claim_due_jobs(self, limit: int = 50) -> list[ReviewSyncJobModel]:
        """Jobs waiting for a retry whose backoff has elapsed."""
        now = datetime.now(UTC)
        jobs = list(
            await self.session.scalars(
                select(ReviewSyncJobModel)
                .where(
                    ReviewSyncJobModel.status == "queued",
                    ReviewSyncJobModel.next_attempt_at.is_not(None),
                    ReviewSyncJobModel.next_attempt_at <= now,
                )
                .order_by(ReviewSyncJobModel.next_attempt_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for job in jobs:
            job.next_attempt_at = None
        return jobs

    async def abandon_stale_runs(self, max_age_seconds: int) -> list[uuid.UUID]:
        """Close runs that can never finish, so a new one is allowed to start."""
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        run_ids = list(
            await self.session.scalars(
                select(ReviewSyncRunModel.id).where(
                    ReviewSyncRunModel.status.in_(ACTIVE_RUN_STATUSES),
                    ReviewSyncRunModel.created_at < cutoff,
                )
            )
        )
        for run_id in run_ids:
            await self.session.execute(
                update(ReviewSyncJobModel)
                .where(
                    ReviewSyncJobModel.run_id == run_id,
                    ReviewSyncJobModel.status.in_(ACTIVE_RUN_STATUSES),
                )
                .values(
                    status="error",
                    error="Прогон закрыт по таймауту",
                    finished_at=datetime.now(UTC),
                    next_attempt_at=None,
                    lease_expires_at=None,
                )
            )
            await self.finalize_run(run_id)
        return run_ids

    async def run_of_job(self, job_id: uuid.UUID) -> ReviewSyncRunModel | None:
        job = await self.session.get(ReviewSyncJobModel, job_id)
        if job is None:
            return None
        return await self.session.get(ReviewSyncRunModel, job.run_id)

    async def finalize_run(self, run_id: uuid.UUID) -> None:
        # Lock the run row first. Two consumers finishing the last two jobs at
        # the same time would otherwise both count each other's job as pending
        # and leave the run running forever; the second finisher now waits for
        # the first to commit and recounts against the committed statuses.
        await self.session.execute(
            select(ReviewSyncRunModel.id).where(ReviewSyncRunModel.id == run_id).with_for_update()
        )
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
        return await self.history_range(seller_id, since, None)

    async def history_range(self, seller_id: uuid.UUID, date_from: date, date_to: date | None) -> list[DailyRatings]:
        conditions = [DailyReviewCountModel.seller_id == seller_id, DailyReviewCountModel.date >= date_from]
        if date_to is not None:
            conditions.append(DailyReviewCountModel.date <= date_to)
        rows = await self.session.scalars(
            select(DailyReviewCountModel)
            .where(*conditions)
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
