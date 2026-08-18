import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.domain import DailyRatings
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


class HistorySellers:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.seller_id = seller_id
        # Two articles of one склейка plus one card WB never glued.
        self.articles = [
            Article(uuid.uuid4(), seller_id, "101", "SKU-101", "Шуруповерт", imt_id=900),
            Article(uuid.uuid4(), seller_id, "102", "SKU-102", "Шуруповерт", imt_id=900),
            Article(uuid.uuid4(), seller_id, "103", "SKU-103", "Пылесос", imt_id=None),
        ]

    async def get(self, seller_id: uuid.UUID):
        return SimpleNamespace(id=seller_id) if seller_id == self.seller_id else None

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.articles


class HistoryReviews:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.rows = [
            DailyRatings(seller_id, "101", date(2026, 8, 17), (1, 0, 0, 0, 10), datetime.now(UTC)),
            DailyRatings(seller_id, "101", date(2026, 8, 18), (1, 0, 0, 0, 12), datetime.now(UTC)),
            DailyRatings(seller_id, "102", date(2026, 8, 18), (0, 0, 0, 2, 5), datetime.now(UTC)),
            DailyRatings(seller_id, "103", date(2026, 8, 18), (0, 0, 0, 0, 3), datetime.now(UTC)),
        ]

    async def history(self, seller_id: uuid.UUID, days: int) -> list[DailyRatings]:
        return self.rows


async def test_history_reports_both_the_article_and_the_whole_card() -> None:
    seller_id = uuid.uuid4()
    sellers = HistorySellers(seller_id)
    service = ReviewSyncService(
        cast(AsyncSession, FakeSession()),
        cast(SellerRepository, sellers),
        cast(ReviewSyncRepository, HistoryReviews(seller_id)),
    )

    history = await service.history(seller_id, 90)
    products = {product.article: product for product in history.products}

    # Per-article numbers stay untouched...
    assert products["101"].snapshots[-1]["ratings"] == {1: 1, 2: 0, 3: 0, 4: 0, 5: 12}
    # ...while the card series sums every nmID of imtID 900, which is what the
    # seller sees on the WB card itself.
    assert products["101"].card_snapshots[-1]["ratings"] == {1: 1, 2: 0, 3: 0, 4: 2, 5: 17}
    assert products["102"].card_snapshots == products["101"].card_snapshots
    # A day only one article reported still appears in the card series.
    assert [snapshot["date"] for snapshot in products["101"].card_snapshots] == ["2026-08-17", "2026-08-18"]
    # Without an imtID the card series is just the article itself.
    assert products["103"].card_snapshots == products["103"].snapshots
