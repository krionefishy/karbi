import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.application import Application
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.modules.wb_reviews.infrastructure.postgres.models import (
    DailyReviewCountModel,
    ReviewSyncJobModel,
    ReviewSyncRunModel,
    TrackedSellerModel,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def test_review_sync_is_persisted_through_postgres_and_outbox() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    seller = SellerModel(name="Review integration seller", catalog_sync_status="success")

    try:
        async with database.session() as session:
            await session.execute(delete(ReviewSyncJobModel))
            await session.execute(delete(ReviewSyncRunModel))
            session.add(seller)
            await session.flush()
            # The run covers whoever is enrolled, so the seller has to join the
            # automation before he can appear in one.
            session.add(TrackedSellerModel(seller_id=seller.id))
            await session.commit()

        async with database.session() as session:
            reviews = ReviewSyncRepository(session)
            service = ReviewSyncService(session, SellerRepository(session), reviews)
            run = await service.request("manual", today)

            jobs = await reviews.jobs_for_run(run.id)
            event = await session.scalar(
                select(OutboxEventModel).where(
                    OutboxEventModel.aggregate_id == seller.id,
                    OutboxEventModel.event_type == "WBReviewSyncRequested",
                )
            )
            assert len(jobs) == 1
            assert event is not None
            assert event.payload["job_id"] == str(jobs[0].id)

        async with database.session() as session:
            reviews = ReviewSyncRepository(session)
            await reviews.upsert_daily_counts(seller.id, yesterday, {"123": (1, 2, 3, 4, 5)})
            await reviews.upsert_daily_counts(seller.id, today, {"123": (2, 3, 4, 5, 7)})
            await session.commit()

        async with database.session() as session:
            history = await ReviewSyncRepository(session).history(seller.id, 7)
            assert [snapshot.ratings[4] for snapshot in history] == [5, 7]
    finally:
        async with database.session() as session:
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller.id))
            await session.execute(delete(DailyReviewCountModel).where(DailyReviewCountModel.seller_id == seller.id))
            await session.execute(delete(ReviewSyncJobModel).where(ReviewSyncJobModel.seller_id == seller.id))
            await session.execute(delete(ReviewSyncRunModel))
            await session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


async def test_review_sync_http_endpoints_use_application_dependencies() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = Application(settings)
    app = application.get_app()

    async with app.router.lifespan_context(app):
        async with application.database.session() as session:
            await session.execute(delete(ReviewSyncJobModel))
            await session.execute(delete(ReviewSyncRunModel))
            await session.execute(delete(TrackedSellerModel))
            await session.commit()

        token = application.token_service.issue_access(uuid.uuid4())
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            started = await client.post("/api/v1/wb/reviews/sync")
            latest = await client.get("/api/v1/wb/reviews/sync/latest")

        assert started.status_code == 202
        assert started.json()["status"] == "success"
        assert latest.status_code == 200
        assert latest.json()["id"] == started.json()["id"]

        async with application.database.session() as session:
            await session.execute(delete(ReviewSyncJobModel))
            await session.execute(delete(ReviewSyncRunModel))
            await session.commit()
