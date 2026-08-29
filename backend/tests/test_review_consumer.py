import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from typing import cast

import pytest

from backend.modules.wb_core.domain import Article
from backend.modules.wb_core.infrastructure.wb import CatalogCard, WBContentClient, WBPermanentError
from backend.modules.wb_reviews.infrastructure.wb import (
    FeedbackAggregation,
    FeedbackProduct,
    WBFeedbackClient,
    WBFeedbackTemporaryError,
)
from backend.storage.pg import Database
from backend.workers.wb_reviews import catalog_consumer as catalog_module
from backend.workers.wb_reviews import review_consumer as consumer_module
from backend.workers.wb_reviews.catalog_consumer import CatalogSyncConsumer
from backend.workers.wb_reviews.review_consumer import InvalidPayloadError, ReviewSyncConsumer


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
        return SimpleNamespace(id=seller_id, archived_at=None) if seller_id == self.seller_id else None

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.articles

    async def ensure_feedback_articles(self, seller_id: uuid.UUID, cards: Sequence[CatalogCard]) -> None:
        self.feedback_only = list(cards)

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.inbox_events.append(event_id)


class FakeReviews:
    def __init__(
        self, job_id: uuid.UUID, *, reschedules: bool = True, claims: bool = True, tracked: bool = True
    ) -> None:
        self.job = SimpleNamespace(id=job_id, status="queued")
        self.saved_counts: dict[str, tuple[int, int, int, int, int]] = {}
        self.completed = False
        self.failed_error: str | None = None
        self.rescheduled_error: str | None = None
        self.lease_seconds: int | None = None
        self._reschedules = reschedules
        self._claims = claims
        self._tracked = tracked

    async def get_job(self, job_id: uuid.UUID):
        return self.job if job_id == self.job.id else None

    async def mark_job_running(self, job_id: uuid.UUID, lease_seconds: int) -> bool:
        if not self._claims:
            return False
        self.job.status = "running"
        self.lease_seconds = lease_seconds
        return True

    async def is_tracked(self, seller_id: uuid.UUID) -> bool:
        return self._tracked

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

    async def aggregate(self, seller_id: str) -> FeedbackAggregation:
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
    async def aggregate(self, seller_id: str) -> FeedbackAggregation:
        raise WBFeedbackTemporaryError("WB Feedbacks API временно недоступен после 10 минут повторов: HTTP 429")


class MustNotBeCalledClient:
    async def aggregate(self, seller_id: str) -> FeedbackAggregation:
        raise AssertionError("WB must not be called for an unclaimed job")


def build_consumer(database: FakeDatabase, client) -> ReviewSyncConsumer:
    return ReviewSyncConsumer(
        cast(Database, database),
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


async def test_review_consumer_skips_a_job_it_could_not_claim(monkeypatch) -> None:
    """Another worker already holds the job, so WB is not called a second time."""
    seller_id, job_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id, claims=False)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, MustNotBeCalledClient()).process(payload(job_id, seller_id, event_id))

    assert not reviews.completed
    assert reviews.failed_error is None
    # The offset is still committed: the message is spent, not poisoned.
    assert sellers.inbox_events == [event_id]


async def test_review_consumer_does_not_write_counts_for_a_detached_seller(monkeypatch) -> None:
    """Detaching purges the history; a job in flight must not resurrect it."""
    seller_id, job_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id, tracked=False)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    await build_consumer(database, FakeFeedbackClient(database)).process(payload(job_id, seller_id, event_id))

    assert reviews.saved_counts == {}
    # The job still closes cleanly so the run can finish.
    assert reviews.completed
    assert reviews.failed_error is None
    assert sellers.inbox_events == [event_id]


async def test_review_consumer_raises_a_skippable_error_for_a_malformed_payload() -> None:
    consumer = build_consumer(FakeDatabase(), MustNotBeCalledClient())

    with pytest.raises(InvalidPayloadError):
        await consumer.process({"event_id": "not-a-uuid"})
    with pytest.raises(InvalidPayloadError):
        await consumer.process(
            {
                "event_id": str(uuid.uuid4()),
                "run_id": str(uuid.uuid4()),
                "job_id": str(uuid.uuid4()),
                "seller_id": str(uuid.uuid4()),
                "snapshot_date": "вчера",
            }
        )


class FakeCatalogSellers:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.seller_id = seller_id
        self.inbox_events: list[uuid.UUID] = []
        self.sync_statuses: list[tuple] = []

    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return False

    async def get(self, seller_id: uuid.UUID):
        return SimpleNamespace(id=seller_id, archived_at=None) if seller_id == self.seller_id else None

    async def set_sync_status(self, seller_id: uuid.UUID, status: str, error: str | None = None) -> None:
        self.sync_statuses.append((status, error))

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.inbox_events.append(event_id)


class InvalidKeyContentClient:
    async def get_catalog(self, seller_id: str):
        raise WBPermanentError("WB Content API: ключ недействителен или не имеет доступа")


class MustNotBeCalledContentClient:
    async def get_catalog(self, seller_id: str):
        raise AssertionError("WB must not be called for a malformed payload")


async def test_catalog_consumer_treats_an_invalid_key_as_permanent(monkeypatch) -> None:
    """A key the gateway rejects for good would otherwise be retried forever."""
    seller_id, event_id = uuid.uuid4(), uuid.uuid4()
    sellers = FakeCatalogSellers(seller_id)
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)
    consumer = CatalogSyncConsumer(
        cast(Database, FakeDatabase()),
        "kafka:9092",
        "test",
        client=cast(WBContentClient, InvalidKeyContentClient()),
    )

    await consumer.process({"event_id": str(event_id), "seller_id": str(seller_id)})

    assert sellers.sync_statuses[-1][0] == "error"
    assert "недействителен" in sellers.sync_statuses[-1][1]
    assert sellers.inbox_events == [event_id]


async def test_catalog_consumer_raises_a_skippable_error_for_a_malformed_payload() -> None:
    consumer = CatalogSyncConsumer(
        cast(Database, FakeDatabase()),
        "kafka:9092",
        "test",
        client=cast(WBContentClient, MustNotBeCalledContentClient()),
    )

    with pytest.raises(InvalidPayloadError):
        await consumer.process({"seller_id": "not-a-uuid"})


class StubKafkaConsumer:
    """Hands out a fixed list of raw messages, then blocks like a real consumer."""

    def __init__(self, messages: list[bytes], *args, **kwargs) -> None:
        self._messages = list(messages)
        self.committed = 0
        self.seeks: list[int] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def getone(self):
        if not self._messages:
            raise asyncio.CancelledError
        return SimpleNamespace(value=self._messages.pop(0), topic="t", partition=0, offset=len(self.seeks))

    async def commit(self) -> None:
        self.committed += 1

    def seek(self, partition, offset) -> None:
        self.seeks.append(offset)


async def test_a_non_json_message_does_not_kill_the_review_consumer(monkeypatch) -> None:
    """A deserializer would raise inside getone(), outside every guard, and the
    restart would re-read the very same message forever."""
    seller_id, job_id, event_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeSellers(seller_id)
    reviews = FakeReviews(job_id)
    monkeypatch.setattr(consumer_module, "SellerRepository", lambda session: sellers)
    monkeypatch.setattr(consumer_module, "ReviewSyncRepository", lambda session: reviews)

    good = json.dumps(payload(job_id, seller_id, event_id)).encode()
    stub: dict = {}

    def build(*args, **kwargs):
        stub["consumer"] = StubKafkaConsumer([b"{not json at all", good], *args, **kwargs)
        return stub["consumer"]

    monkeypatch.setattr(consumer_module, "AIOKafkaConsumer", build)

    with contextlib.suppress(asyncio.CancelledError):
        await build_consumer(database, FakeFeedbackClient(database)).run()

    consumer = stub["consumer"]
    # Both messages were committed: the poison one skipped, the good one worked.
    assert consumer.committed == 2
    assert consumer.seeks == []
    assert reviews.completed


async def test_a_non_json_message_does_not_kill_the_catalog_consumer(monkeypatch) -> None:
    seller_id, event_id = uuid.uuid4(), uuid.uuid4()
    database = FakeDatabase()
    sellers = FakeCatalogSellers(seller_id)
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)

    good = json.dumps({"event_id": str(event_id), "seller_id": str(seller_id)}).encode()
    stub: dict = {}

    def build(*args, **kwargs):
        stub["consumer"] = StubKafkaConsumer([b"<html>oops</html>", good], *args, **kwargs)
        return stub["consumer"]

    monkeypatch.setattr(catalog_module, "AIOKafkaConsumer", build)

    class EmptyCatalog:
        async def get_catalog(self, seller_id: str):
            return SimpleNamespace(active=[], archived=[], archived_available=[])

    async def upsert_catalog(seller_id, *, active, archived, archived_available) -> None:
        return None

    sellers.upsert_catalog = upsert_catalog  # type: ignore[attr-defined]

    consumer = CatalogSyncConsumer(
        cast(Database, database),
        "kafka:9092",
        "test",
        client=cast(WBContentClient, EmptyCatalog()),
    )
    with contextlib.suppress(asyncio.CancelledError):
        await consumer.run()

    assert stub["consumer"].committed == 2
    assert stub["consumer"].seeks == []
