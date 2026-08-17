import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import cast

from backend.modules.wb_core.infrastructure.wb import WBContentClient
from backend.modules.wb_reviews.infrastructure.wb import FeedbackAggregation, FeedbackProduct, WBFeedbackClient
from backend.shared.security import CredentialCipher
from backend.storage.pg import Database
from backend.workers.wb_reviews import review_consumer as consumer_module
from backend.workers.wb_reviews.review_consumer import ReviewSyncConsumer


class FakeSession:
    async def commit(self) -> None:
        return None


class FakeDatabase:
    def __init__(self) -> None:
        self.open_sessions = 0

    @asynccontextmanager
    async def session(self) -> AsyncIterator[FakeSession]:
        self.open_sessions += 1
        try:
            yield FakeSession()
        finally:
            self.open_sessions -= 1


class FakeSellers:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.seller_id = seller_id
        self.saved_articles: list[dict[str, str]] = []
        self.inbox_events: list[uuid.UUID] = []

    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return False

    async def get(self, seller_id: uuid.UUID):
        return SimpleNamespace(id=seller_id) if seller_id == self.seller_id else None

    async def get_credential(self, seller_id: uuid.UUID):
        return SimpleNamespace(encrypted_api_key="encrypted") if seller_id == self.seller_id else None

    async def upsert_articles(self, seller_id: uuid.UUID, articles: list[dict[str, str]]) -> None:
        self.saved_articles = articles

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.inbox_events.append(event_id)


class FakeReviews:
    def __init__(self, job_id: uuid.UUID) -> None:
        self.job = SimpleNamespace(id=job_id, status="queued")
        self.saved_counts: dict[str, tuple[int, int, int, int, int]] = {}
        self.completed = False

    async def get_job(self, job_id: uuid.UUID):
        return self.job if job_id == self.job.id else None

    async def mark_job_running(self, job_id: uuid.UUID) -> bool:
        self.job.status = "running"
        return True

    async def mark_run_running(self, run_id: uuid.UUID) -> None:
        return None

    async def upsert_daily_counts(self, seller_id: uuid.UUID, snapshot_date: date, article_counts) -> None:
        self.saved_counts = article_counts

    async def complete_job(self, job_id: uuid.UUID, product_count: int, feedback_count: int) -> None:
        self.completed = True

    async def fail_job(self, job_id: uuid.UUID, error: str) -> None:
        raise AssertionError(error)

    async def finalize_run(self, run_id: uuid.UUID) -> None:
        return None


class FakeCatalogClient:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    async def get_articles(self, api_key: str) -> list[dict[str, str]]:
        assert self.database.open_sessions == 0
        return [{"article": "123", "vendor_code": "SKU-123", "name": "Catalog product"}]


class FakeFeedbackClient:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    async def aggregate(self, api_key: str) -> FeedbackAggregation:
        assert self.database.open_sessions == 0
        return FeedbackAggregation(
            counts={"123": (1, 2, 3, 4, 5), "456": (0, 0, 0, 1, 2)},
            products={"456": FeedbackProduct("456", "SKU-456", "Feedback product")},
            feedback_count=18,
        )


class FakeCipher:
    def decrypt(self, token: str) -> str:
        return "api-key"


async def test_review_consumer_closes_database_session_while_calling_wb(monkeypatch) -> None:
    seller_id = uuid.uuid4()
    job_id = uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    consumer = ReviewSyncConsumer(
        cast(Database, database),
        cast(CredentialCipher, FakeCipher()),
        "kafka:9092",
        "test",
        5000,
    )
    consumer.catalog_client = cast(WBContentClient, FakeCatalogClient(database))
    consumer.feedback_client = cast(WBFeedbackClient, FakeFeedbackClient(database))
    event_id = uuid.uuid4()

    await consumer.process(
        {
            "event_id": str(event_id),
            "run_id": str(uuid.uuid4()),
            "job_id": str(job_id),
            "seller_id": str(seller_id),
            "snapshot_date": "2026-08-18",
        }
    )

    assert reviews.completed
    assert reviews.saved_counts["123"] == (1, 2, 3, 4, 5)
    assert reviews.saved_counts["456"] == (0, 0, 0, 1, 2)
    assert sellers.inbox_events == [event_id]
