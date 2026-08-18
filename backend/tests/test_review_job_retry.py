from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.modules.wb_reviews.infrastructure.postgres.models import ReviewSyncJobModel, ReviewSyncRunModel
from backend.shared.settings import load_settings
from backend.storage.pg import Database

RATE_LIMITED = "WB Feedbacks API временно недоступен: HTTP 429"


@pytest_asyncio.fixture
async def sync_run() -> AsyncIterator[tuple[Database, SellerModel]]:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    seller = SellerModel(name="Retry seller", is_active=True, catalog_sync_status="success")
    async with database.session() as session:
        await session.execute(delete(ReviewSyncJobModel))
        await session.execute(delete(ReviewSyncRunModel))
        session.add(seller)
        await session.commit()
    try:
        yield database, seller
    finally:
        async with database.session() as session:
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller.id))
            await session.execute(delete(ReviewSyncJobModel))
            await session.execute(delete(ReviewSyncRunModel))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


async def create_run(database: Database, seller: SellerModel) -> tuple[ReviewSyncRunModel, ReviewSyncJobModel]:
    async with database.session() as session:
        reviews = ReviewSyncRepository(session)
        run, jobs = await reviews.create_run("manual", datetime.now(UTC).date(), [seller.id])
        await session.commit()
        return run, jobs[0]


@pytest.mark.asyncio
async def test_a_throttled_job_is_requeued_with_backoff_and_redispatched(sync_run) -> None:
    database, seller = sync_run
    run, job = await create_run(database, seller)

    async with database.session() as session:
        reviews = ReviewSyncRepository(session)
        await reviews.mark_job_running(job.id, lease_seconds=600)
        assert await reviews.reschedule_job(job.id, RATE_LIMITED, max_attempts=3, backoff_seconds=60)
        await session.commit()

    async with database.session() as session:
        stored = await session.get(ReviewSyncJobModel, job.id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.attempts == 1
        assert stored.next_attempt_at is not None
        # The run stays open, so the day is not lost to one 429.
        assert (await session.get(ReviewSyncRunModel, run.id)).finished_at is None
        # Backoff has not elapsed yet, so nothing is dispatched.
        assert await ReviewSyncRepository(session).claim_due_jobs() == []

    async with database.session() as session:
        stored = await session.get(ReviewSyncJobModel, job.id)
        stored.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with database.session() as session:
        report = await ReviewSyncService(
            session, SellerRepository(session), ReviewSyncRepository(session)
        ).run_maintenance(max_attempts=3, max_run_age_seconds=21_600)

    assert report.requeued_jobs == 1
    async with database.session() as session:
        events = list(
            await session.scalars(
                select(OutboxEventModel).where(
                    OutboxEventModel.aggregate_id == seller.id,
                    OutboxEventModel.event_type == "WBReviewSyncRequested",
                )
            )
        )
    # The fixture creates the run directly, so this is the retry dispatch itself:
    # a fresh event id, which is what keeps the inbox from swallowing the retry.
    assert len(events) == 1
    assert events[0].payload["job_id"] == str(job.id)
    assert events[0].payload["event_id"] == str(events[0].id)


@pytest.mark.asyncio
async def test_a_job_fails_for_good_once_attempts_run_out(sync_run) -> None:
    database, seller = sync_run
    _, job = await create_run(database, seller)

    async with database.session() as session:
        reviews = ReviewSyncRepository(session)
        # First attempt earns a retry, the second exhausts the allowance of two.
        await reviews.mark_job_running(job.id, lease_seconds=600)
        assert await reviews.reschedule_job(job.id, RATE_LIMITED, max_attempts=2, backoff_seconds=1)
        await reviews.mark_job_running(job.id, lease_seconds=600)
        assert not await reviews.reschedule_job(job.id, RATE_LIMITED, max_attempts=2, backoff_seconds=1)
        await session.commit()

    async with database.session() as session:
        stored = await session.get(ReviewSyncJobModel, job.id)
        assert stored.status == "error"
        assert stored.error == RATE_LIMITED


@pytest.mark.asyncio
async def test_an_expired_lease_returns_the_job_to_the_queue(sync_run) -> None:
    database, seller = sync_run
    _, job = await create_run(database, seller)

    async with database.session() as session:
        reviews = ReviewSyncRepository(session)
        await reviews.mark_job_running(job.id, lease_seconds=600)
        stored = await session.get(ReviewSyncJobModel, job.id)
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with database.session() as session:
        reviews = ReviewSyncRepository(session)
        assert await reviews.expire_leases(max_attempts=3) == 1
        await session.commit()

    async with database.session() as session:
        stored = await session.get(ReviewSyncJobModel, job.id)
        assert stored.status == "queued"
        assert stored.next_attempt_at is not None


@pytest.mark.asyncio
async def test_a_stale_run_is_closed_so_a_new_one_can_start(sync_run) -> None:
    database, seller = sync_run
    run, job = await create_run(database, seller)

    async with database.session() as session:
        stored_run = await session.get(ReviewSyncRunModel, run.id)
        stored_run.created_at = datetime.now(UTC) - timedelta(hours=12)
        await session.commit()

    async with database.session() as session:
        report = await ReviewSyncService(
            session, SellerRepository(session), ReviewSyncRepository(session)
        ).run_maintenance(max_attempts=3, max_run_age_seconds=3600)

    assert report.abandoned_runs == 1
    async with database.session() as session:
        assert (await session.get(ReviewSyncRunModel, run.id)).status == "error"
        assert (await session.get(ReviewSyncJobModel, job.id)).status == "error"
        # A closed run no longer counts as active, so the next sync is allowed.
        assert await ReviewSyncRepository(session).active_run() is None
