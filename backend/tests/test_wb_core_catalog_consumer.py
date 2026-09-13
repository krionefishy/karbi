import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import pytest

from backend.modules.wb_core.application import CatalogOutcome, MirrorService, SellerGoneError
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.storage.pg import Database
from backend.workers.wb_core import catalog_consumer as catalog_module
from backend.workers.wb_core.catalog_consumer import CatalogSyncConsumer, InvalidPayloadError


class FakeSession:
    async def commit(self) -> None:
        return None


class FakeDatabase:
    @asynccontextmanager
    async def session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class FakeSellers:
    def __init__(self, processed: bool = False) -> None:
        self.processed = processed
        self.inbox_events: list[uuid.UUID] = []

    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return self.processed

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.inbox_events.append(event_id)


class FakeMirror:
    def __init__(self, outcome: Exception | None = None) -> None:
        self.outcome = outcome
        self.synced: list[uuid.UUID] = []

    async def sync_catalog(self, seller_id: uuid.UUID, *, now=None) -> CatalogOutcome:
        self.synced.append(seller_id)
        if self.outcome is not None:
            raise self.outcome
        return CatalogOutcome(1, 0, True)


def consumer(mirror: FakeMirror) -> CatalogSyncConsumer:
    return CatalogSyncConsumer(cast(Database, FakeDatabase()), "kafka:9092", "test", mirror=cast(MirrorService, mirror))


def event(seller_id: uuid.UUID, event_id: uuid.UUID) -> dict:
    return {"event_id": str(event_id), "seller_id": str(seller_id)}


async def test_a_synced_catalog_marks_the_event_processed(monkeypatch) -> None:
    seller_id, event_id = uuid.uuid4(), uuid.uuid4()
    sellers = FakeSellers()
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)
    mirror = FakeMirror()

    await consumer(mirror).process(event(seller_id, event_id))

    assert mirror.synced == [seller_id]
    assert sellers.inbox_events == [event_id]


async def test_a_processed_event_is_not_synced_twice(monkeypatch) -> None:
    sellers = FakeSellers(processed=True)
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)
    mirror = FakeMirror()

    await consumer(mirror).process(event(uuid.uuid4(), uuid.uuid4()))

    assert mirror.synced == []
    assert sellers.inbox_events == []


async def test_an_invalid_key_is_final_for_the_event(monkeypatch) -> None:
    """A key the gateway rejects for good would otherwise be retried forever."""
    seller_id, event_id = uuid.uuid4(), uuid.uuid4()
    sellers = FakeSellers()
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)
    mirror = FakeMirror(WBPermanentError("WB Content API: ключ недействителен"))

    await consumer(mirror).process(event(seller_id, event_id))

    assert sellers.inbox_events == [event_id]


async def test_a_gone_seller_is_final_for_the_event(monkeypatch) -> None:
    seller_id, event_id = uuid.uuid4(), uuid.uuid4()
    sellers = FakeSellers()
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)

    await consumer(FakeMirror(SellerGoneError(str(seller_id)))).process(event(seller_id, event_id))

    assert sellers.inbox_events == [event_id]


async def test_a_temporary_failure_leaves_the_event_for_a_retry(monkeypatch) -> None:
    sellers = FakeSellers()
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)

    with pytest.raises(WBTemporaryError):
        await consumer(FakeMirror(WBTemporaryError("WB 503"))).process(event(uuid.uuid4(), uuid.uuid4()))

    assert sellers.inbox_events == []


async def test_a_malformed_payload_raises_a_skippable_error() -> None:
    with pytest.raises(InvalidPayloadError):
        await consumer(FakeMirror()).process({"seller_id": "not-a-uuid"})


class StubKafkaConsumer:
    """Hands out a fixed list of raw messages, then blocks like a real consumer."""

    def __init__(self, messages: list[bytes], *args, **kwargs) -> None:
        self._messages = list(messages)
        self.committed = 0
        self.seeks: list[int] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def getone(self):
        if not self._messages:
            raise asyncio.CancelledError
        return SimpleNamespace(value=self._messages.pop(0), topic="t", partition=0, offset=len(self.seeks))

    async def commit(self) -> None:
        self.committed += 1

    def seek(self, partition, offset) -> None:
        self.seeks.append(offset)


async def test_a_non_json_message_does_not_kill_the_consumer(monkeypatch) -> None:
    """A deserializer would raise inside getone(), outside every guard, and the
    restart would re-read the very same message forever."""
    sellers = FakeSellers()
    monkeypatch.setattr(catalog_module, "SellerRepository", lambda session: sellers)
    good = json.dumps(event(uuid.uuid4(), uuid.uuid4())).encode()
    stub: dict = {}

    def build(*args, **kwargs):
        stub["consumer"] = StubKafkaConsumer([b"<html>oops</html>", good], *args, **kwargs)
        return stub["consumer"]

    monkeypatch.setattr(catalog_module, "AIOKafkaConsumer", build)
    mirror = FakeMirror()

    with contextlib.suppress(asyncio.CancelledError):
        await consumer(mirror).run()

    assert stub["consumer"].committed == 2
    assert stub["consumer"].seeks == []
    assert len(mirror.synced) == 1
