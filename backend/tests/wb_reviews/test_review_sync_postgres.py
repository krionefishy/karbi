from datetime import UTC, datetime, timedelta
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.factory import Application
from backend.modules.wb_core.domain import MIRROR_REVIEWS, ReviewFact
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel, OutboxEventModel, SellerModel
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
from backend.tests.operators import operator_token


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

        token = await operator_token(application)
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


async def test_group_changes_and_ratings_go_through_http() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = Application(settings)
    app = application.get_app()
    today = datetime.now(UTC).date()

    async with app.router.lifespan_context(app):
        seller = SellerModel(name="Review groups seller", catalog_sync_status="success")
        async with application.database.session() as session:
            session.add(seller)
            await session.flush()
            session.add_all(
                [
                    ArticleModel(seller_id=seller.id, article="101", name="Шуруповерт", imt_id=900, photo_count=22),
                    ArticleModel(seller_id=seller.id, article="102", name="Шуруповерт", imt_id=900, photo_count=0),
                    ArticleModel(seller_id=seller.id, article="201", name="Компрессор", imt_id=777, photo_count=0),
                ]
            )
            await ReviewSyncRepository(session).upsert_daily_counts(
                seller.id, today, {"101": (0, 0, 0, 1, 9), "102": (0, 0, 0, 0, 2), "201": (1, 0, 0, 0, 1)}
            )
            await MirrorRepository(session).replace_reviews(
                seller.id,
                [ReviewFact("101", (0, 0, 0, 1, 9), None, None, wb_rating=Decimal("4.90"))],
                now=datetime.now(UTC),
            )
            await MirrorRepository(session).record_attempt(seller.id, MIRROR_REVIEWS, now=datetime.now(UTC))
            await MirrorRepository(session).mark_collected(seller.id, MIRROR_REVIEWS, now=datetime.now(UTC))
            await session.commit()

        try:
            token = await operator_token(application)
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                base = f"/api/v1/wb/reviews/sellers/{seller.id}"

                async def change(article: str, body: dict) -> int:
                    return (await client.put(f"{base}/products/{article}/group", json=body)).status_code

                attached = await change("201", {"action": "attach", "main_article": "101"})
                no_target = await change("201", {"action": "attach"})
                to_self = await change("201", {"action": "attach", "main_article": "201"})
                missing = await change("999", {"action": "detach"})
                bad_action = await change("201", {"action": "explode"})
                history = await client.get(f"{base}/history")

            assert (attached, no_target, to_self, missing, bad_action) == (204, 400, 400, 404, 422)
            products = {product["article"]: product for product in history.json()["products"]}
            assert (products["101"]["group_role"], products["101"]["rating"]) == ("main", 4.9)
            assert (products["201"]["group_role"], products["201"]["group_manual"]) == ("member", True)
            assert (products["201"]["rating"], products["201"]["rating_source"]) == (4.9, "group")
            assert products["101"]["group_snapshots"][-1]["ratings"] == {"1": 1, "2": 0, "3": 0, "4": 1, "5": 12}
            assert products["102"]["photo_count"] == 0
        finally:
            async with application.database.session() as session:
                await ReviewSyncRepository(session).purge_seller(seller.id)
                await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
                await session.commit()
