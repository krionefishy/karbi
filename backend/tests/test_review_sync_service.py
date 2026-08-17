import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class FakeSellers:
    def __init__(self) -> None:
        self.seller_ids = [uuid.uuid4(), uuid.uuid4()]

    async def list_sellers(self) -> list[Seller]:
        return [
            Seller(seller_id, f"Seller {index}", 0, "success", None, None)
            for index, seller_id in enumerate(self.seller_ids)
        ]


class FakeReviews:
    def __init__(self) -> None:
        self.locked = False

    async def lock_run_creation(self) -> None:
        self.locked = True

    async def active_run(self):
        return None

    async def run_for_date(self, snapshot_date: date):
        return None

    async def create_run(self, trigger: str, snapshot_date: date, seller_ids: list[uuid.UUID]):
        run = SimpleNamespace(
            id=uuid.uuid4(),
            trigger=trigger,
            snapshot_date=snapshot_date,
            status="queued",
            total_sellers=len(seller_ids),
            completed_sellers=0,
            failed_sellers=0,
            created_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
        )
        jobs = [SimpleNamespace(id=uuid.uuid4(), seller_id=seller_id) for seller_id in seller_ids]
        return run, jobs

    def to_domain(self, model):
        return ReviewSyncRepository.to_domain(model)


async def test_manual_review_sync_creates_one_outbox_event_per_seller() -> None:
    session = FakeSession()
    sellers = FakeSellers()
    reviews = FakeReviews()
    service = ReviewSyncService(
        cast(AsyncSession, session),
        cast(SellerRepository, sellers),
        cast(ReviewSyncRepository, reviews),
    )

    run = await service.request("manual", date(2026, 8, 18))

    events = [item for item in session.added if isinstance(item, OutboxEventModel)]
    assert reviews.locked
    assert run.total_sellers == 2
    assert {event.aggregate_id for event in events} == set(sellers.seller_ids)
    assert all(event.payload["snapshot_date"] == "2026-08-18" for event in events)
    assert session.commits == 1
