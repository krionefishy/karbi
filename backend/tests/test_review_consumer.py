import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import cast

from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.wb import CatalogCard
from backend.modules.wb_reviews.infrastructure.wb import (
    FeedbackAggregation,
    FeedbackProduct,
    WBFeedbackClient,
    WBFeedbackTemporaryError,
)
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
        self.articles = [
            Article(uuid.uuid4(), seller_id, "123", "SKU-123", "Catalog product", imt_id=77),
            Article(uuid.uuid4(), seller_id, "789", "SKU-789", "Archived product", state="archived"),
        ]
        self.feedback_only: list[CatalogCard] = []
        self.inbox_events: list[uuid.UUID] = []

    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return False

    async def get(self, seller_id: uuid.UUID):
        return SimpleNamespace(id=seller_id) if seller_id == self.seller_id else None

    async def get_credential(self, seller_id: uuid.UUID):
        return SimpleNamespace(encrypted_api_key="encrypted") if seller_id == self.seller_id else None

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.articles

    async def ensure_feedback_articles(self, seller_id: uuid.UUID, cards: Sequence[CatalogCard]) -> None:
        self.feedback_only = list(cards)

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.inbox_events.append(event_id)


class FakeReviews:
    def __init__(self, job_id: uuid.UUID, *, reschedules: bool = True) -> None:
        self.job = SimpleNamespace(id=job_id, status="queued")
        self.saved_counts: dict[str, tuple[int, int, int, int, int]] = {}
        self.completed = False
        self.failed_error: str | None = None
        self.rescheduled_error: str | None = None
        self.lease_seconds: int | None = None
        self._reschedules = reschedules

    async def get_job(self, job_id: uuid.UUID):
        return self.job if job_id == self.job.id else None

    async def mark_job_running(self, job_id: uuid.UUID, lease_seconds: int) -> bool:
        self.job.status = "running"
        self.lease_seconds = lease_seconds
        return True

    async def mark_run_running(self, run_id: uuid.UUID) -> None:
        return None

    async def upsert_daily_counts(self, seller_id: uuid.UUID, snapshot_date: date, article_counts) -> None:
        self.saved_counts = article_counts

    async def complete_job(self, job_id: uuid.UUID, product_count: int, feedback_count: int) -> None:
        self.completed = True

    async def fail_job(self, job_id: uuid.UUID, error: str) -> None:
        self.failed_error = error
        self.job.status = "error"

    async def reschedule_job(self, job_id: uuid.UUID, error: str, *, max_attempts: int, backoff_seconds: int) -> bool:
        self.rescheduled_error = error
        if self._reschedules:
            self.job.status = "queued"
        return self._reschedules

    async def finalize_run(self, run_id: uuid.UUID) -> None:
        return None


class FakeFeedbackClient:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database

    async def aggregate(self, api_key: str) -> FeedbackAggregation:
        assert self.database.open_sessions == 0
        return FeedbackAggregation(
            counts={"123": (1, 2, 3, 4, 5), "456": (0, 0, 0, 1, 2)},
            products={
                "123": FeedbackProduct("123", "SKU-123", "Catalog product", imt_id=77),
                "456": FeedbackProduct("456", "SKU-456", "Feedback product", imt_id=88),
            },
            feedback_count=18,
        )


class RateLimitedFeedbackClient:
    async def aggregate(self, api_key: str) -> FeedbackAggregation:
        raise WBFeedbackTemporaryError("WB Feedbacks API временно недоступен после 10 минут повторов: HTTP 429")


class FakeCipher:
    def decrypt(self, token: str) -> str:
        return "api-key"


def build_consumer(database: FakeDatabase, client) -> ReviewSyncConsumer:
    return ReviewSyncConsumer(
        cast(Database, database),
        cast(CredentialCipher, FakeCipher()),
        "kafka:9092",
        "test",
        5000,
        lease_seconds=900,
        client=cast(WBFeedbackClient, client),
    )


def payload(job_id: uuid.UUID, seller_id: uuid.UUID, event_id: uuid.UUID) -> dict:
    return {
        "event_id": str(event_id),
        "run_id": str(uuid.uuid4()),
        "job_id": str(job_id),
        "seller_id": str(seller_id),
        "snapshot_date": "2026-08-18",
    }


async def test_review_consumer_closes_database_session_while_calling_wb(monkeypatch) -> None:
    seller_id, job_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, FakeFeedbackClient(database)).process(payload(job_id, seller_id, event_id))

    assert reviews.completed
    assert reviews.failed_error is None
    assert reviews.lease_seconds == 900
    assert sellers.inbox_events == [event_id]


async def test_review_consumer_counts_every_known_article_including_archived(monkeypatch) -> None:
    seller_id, job_id = uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, FakeFeedbackClient(database)).process(payload(job_id, seller_id, uuid.uuid4()))

    assert reviews.saved_counts["123"] == (1, 2, 3, 4, 5)
    assert reviews.saved_counts["456"] == (0, 0, 0, 1, 2)
    # An archived card keeps its row, otherwise its series would look like a gap.
    assert reviews.saved_counts["789"] == (0, 0, 0, 0, 0)
    # Only the article missing from the catalog is registered as feedback-only.
    assert [card.article for card in sellers.feedback_only] == ["456"]
    assert sellers.feedback_only[0].imt_id == 88


async def test_review_consumer_reschedules_a_rate_limited_job(monkeypatch) -> None:
    seller_id, job_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, RateLimitedFeedbackClient()).process(payload(job_id, seller_id, event_id))

    assert reviews.rescheduled_error is not None
    assert "HTTP 429" in reviews.rescheduled_error
    assert reviews.failed_error is None
    assert reviews.job.status == "queued"
    # The offset is still committed; the retry arrives as a fresh event.
    assert sellers.inbox_events == [event_id]


async def test_review_consumer_fails_the_job_once_attempts_run_out(monkeypatch) -> None:
    seller_id, job_id = uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id, reschedules=False)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, RateLimitedFeedbackClient()).process(payload(job_id, seller_id, uuid.uuid4()))

    assert reviews.failed_error is not None
    assert "HTTP 429" in reviews.failed_error
    assert reviews.job.status == "error"
