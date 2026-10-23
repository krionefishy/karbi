"""Подписчики S3 (на фейковом хранилище), сверка и сборка консьюмеров воркера."""

import os
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from botocore.exceptions import ClientError
from sqlalchemy import select, update

from backend.modules.wb_screenshot_control.application.events import EVENT_DELETE, EVENT_UPLOAD
from backend.modules.wb_screenshot_control.application.inbox import InvalidEventError
from backend.modules.wb_screenshot_control.application.ingest import IngestResult, IngestService
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.reconcile import StorageReconciler
from backend.modules.wb_screenshot_control.application.storage import (
    MAX_UPLOAD_ATTEMPTS,
    ObjectStorageService,
    delete_event,
)
from backend.modules.wb_screenshot_control.infrastructure import wiring
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import StorageObjectModel
from backend.modules.wb_screenshot_control.infrastructure.storage import S3BlobStore, StagingStore
from backend.modules.wb_screenshot_control.infrastructure.storage import staging as staging_module
from backend.storage.pg import Database
from backend.storage.s3 import S3Client
from backend.tests.wb_screenshot_control.fakes import (
    BASE,
    FakeDecider,
    FakeExtractor,
    MemoryBlobStore,
    reset,
    screen,
    settings_for,
    take,
)
from backend.workers.wb_screenshot_control.main import ScreenshotControlWorkerApplication

USER = uuid.UUID("00000000-0000-4000-8000-000000000011")


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(BASE.database.url, pool_size=3, max_overflow=2)
    await reset(database)
    try:
        yield database
    finally:
        await reset(database)
        await database.disconnect()


@pytest.fixture
def staging(tmp_path: Path) -> StagingStore:
    return StagingStore(tmp_path / "staging", max_used_percent=99)


@pytest.fixture
def blobs() -> MemoryBlobStore:
    return MemoryBlobStore()


async def accepted(
    database: Database, staging: StagingStore, seed: int, *, bucket: str = "screens-test"
) -> IngestResult:
    async with database.session() as session:
        return await IngestService(session, staging, BASE.screenshot_control, bucket=bucket).ingest(
            USER, "web", screen(seed), client_submission_id=str(uuid.uuid4()), outcome="positive", outcome_source="form"
        )


async def stored_object(database: Database) -> StorageObjectModel:
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel).where(StorageObjectModel.kind == "orig"))
    assert stored is not None
    return stored


async def upload_payload(database: Database) -> dict[str, Any]:
    rows = await take(database, EVENT_UPLOAD)
    assert len(rows) == 1
    return rows[0].payload


async def test_upload_puts_the_object_and_clears_staging(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    result = await accepted(database, staging, 1)
    payload = await upload_payload(database)
    path = payload["staging_path"]
    service = ObjectStorageService(database, staging, blobs)

    await service.upload(payload)
    stored = await stored_object(database)
    assert stored.state == "stored" and stored.stored_at is not None and stored.staging_path is None
    assert stored.attempts == 1 and stored.last_error is None
    assert blobs.objects[stored.s3_key] == (screen(1), "image/png")
    assert not await staging.exists(path)

    blobs.objects.clear()
    await service.upload(payload)  # повторная доставка того же события
    assert blobs.objects == {}
    await service.upload({**payload, "event_id": str(uuid.uuid4())})  # новое событие на уже выгруженный
    assert blobs.objects == {} and (await stored_object(database)).state == "stored"
    assert result.sha256 in stored.s3_key


async def test_an_unreachable_s3_keeps_the_file_staged_for_the_reconciler(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 2)
    payload = await upload_payload(database)
    blobs.down = True

    await ObjectStorageService(database, staging, blobs).upload(payload)
    stored = await stored_object(database)
    assert stored.state == "staged" and "unreachable" in (stored.last_error or "")
    assert await staging.exists(payload["staging_path"])

    blobs.down = False
    later = datetime.now(UTC) + timedelta(minutes=BASE.screenshot_control.staging_stale_minutes + 1)
    report = await StorageReconciler(database, staging, blobs, BASE.screenshot_control).run(later)
    assert report.republished == 1
    retry = await upload_payload(database)
    await ObjectStorageService(database, staging, blobs).upload(retry)
    assert (await stored_object(database)).state == "stored"


async def test_a_lost_staging_file_is_resolved_by_what_s3_has(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 3)
    payload = await upload_payload(database)
    await staging.delete(payload["staging_path"])
    blobs.objects[payload["key"]] = (screen(3), "image/png")
    await ObjectStorageService(database, staging, blobs).upload(payload)
    assert (await stored_object(database)).state == "stored"

    await reset(database)
    await accepted(database, staging, 4)
    payload = await upload_payload(database)
    await staging.delete(payload["staging_path"])
    await ObjectStorageService(database, staging, blobs).upload(payload)
    stored = await stored_object(database)
    assert stored.state == "failed" and "lost" in (stored.last_error or "")


async def test_delete_removes_the_object_once(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 5)
    service = ObjectStorageService(database, staging, blobs)
    await service.upload(await upload_payload(database))
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel).with_for_update())
        assert stored is not None
        stored.state = "delete_requested"
        publish(session, delete_event(stored, "retention"))
        await session.commit()
    payload = (await take(database, EVENT_DELETE))[0].payload

    blobs.down = True
    await service.delete(payload)
    stored = await stored_object(database)
    assert stored.state == "delete_requested" and stored.last_error

    blobs.down = False
    retry = {**payload, "event_id": str(uuid.uuid4())}
    await service.delete(retry)
    stored = await stored_object(database)
    assert stored.state == "deleted" and stored.deleted_at is not None and blobs.objects == {}
    await service.delete(retry)
    assert blobs.deleted == [stored.s3_key]


async def test_delete_skips_an_object_that_came_back(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 6)
    stored = await stored_object(database)
    payload = delete_event(stored, "retention").payload() | {"event_id": str(uuid.uuid4())}
    await ObjectStorageService(database, staging, blobs).delete(payload)  # объект staged, не delete_requested
    assert blobs.deleted == [] and (await stored_object(database)).state == "staged"


async def test_unparseable_events_are_refused(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    service = ObjectStorageService(database, staging, blobs)
    with pytest.raises(InvalidEventError):
        await service.upload({"file_id": "nope"})
    with pytest.raises(InvalidEventError):
        await service.delete({"file_id": str(uuid.uuid4()), "kind": "orig", "bucket": "b", "key": "x", "reason": "x"})


async def test_reconcile_waits_for_s3_and_then_fills_the_bucket(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 7, bucket="")
    later = datetime.now(UTC) + timedelta(hours=1)

    without_s3 = await StorageReconciler(database, staging, None, BASE.screenshot_control).run(later)
    assert without_s3.republished == 0 and without_s3.oldest_staged_minutes is not None
    assert await take(database, EVENT_UPLOAD) == []

    with_s3 = await StorageReconciler(database, staging, blobs, BASE.screenshot_control).run(later)
    assert with_s3.republished == 1
    payload = await upload_payload(database)
    assert payload["bucket"] == blobs.bucket
    assert (await stored_object(database)).bucket == blobs.bucket


async def test_reconcile_gives_up_after_too_many_attempts_and_flags_missing_objects(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 8)
    async with database.session() as session:
        await session.execute(update(StorageObjectModel).values(attempts=MAX_UPLOAD_ATTEMPTS))
        await session.commit()
    later = datetime.now(UTC) + timedelta(hours=1)
    report = await StorageReconciler(database, staging, blobs, BASE.screenshot_control).run(later)
    assert report.failed == 1 and (await stored_object(database)).state == "failed"

    await reset(database)
    await accepted(database, staging, 9)
    await ObjectStorageService(database, staging, blobs).upload(await upload_payload(database))
    blobs.objects.clear()
    report = await StorageReconciler(database, staging, blobs, BASE.screenshot_control).run(later)
    stored = await stored_object(database)
    assert report.missing == 1 and stored.state == "failed" and "missing" in (stored.last_error or "")


async def test_reconcile_removes_day_old_orphans_from_staging(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore
) -> None:
    await accepted(database, staging, 10)
    live = (await stored_object(database)).staging_path or ""
    await staging.write("orig/zz/orphan.png", b"left by a crashed upload")
    day_ago = time.time() - 25 * 3600
    for path in (live, "orig/zz/orphan.png"):
        os.utime(staging.resolve(path), (day_ago, day_ago))

    report = await StorageReconciler(database, staging, blobs, BASE.screenshot_control).run(datetime.now(UTC))
    assert report.orphans_removed == 1
    assert await staging.exists(live) and not await staging.exists("orig/zz/orphan.png")


async def test_staging_writes_atomically_and_stays_inside_its_root(staging: StagingStore) -> None:
    await staging.write("orig/ab/file.png", b"one")
    await staging.write("orig/ab/file.png", b"two")
    assert await staging.read("orig/ab/file.png") == b"two"
    assert [path for path, _ in await staging.older_than(0)] == ["orig/ab/file.png"]
    assert staging.has_room()
    for bad in ("../escape.png", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            staging.resolve(bad)
    assert not StagingStore(staging.root, max_used_percent=0).has_room()


async def test_the_staging_cap_counts_the_directory_not_the_disk(tmp_path: Path) -> None:
    """Без S3 файлы копятся в staging: предел каталога срабатывает задолго до заполнения тома."""
    capped = StagingStore(tmp_path / "capped", max_used_percent=100, max_bytes=1000)
    assert capped.has_room() and capped.used_bytes() == 0
    await capped.write("orig/aa/one.png", b"x" * 600)
    assert capped.has_room() and capped.used_bytes() == 600  # кэш размера дополняется записью
    await capped.write("orig/bb/two.png", b"x" * 500)
    assert not capped.has_room()
    percent = capped.used_percent()
    assert percent is not None and percent >= 110.0

    other = StagingStore(tmp_path / "capped", max_used_percent=100, max_bytes=10_000)
    assert other.has_room(), "предел — у каждого хранилища свой, размер каталога общий"


def test_the_staging_size_is_measured_again_after_the_cache_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "measured"
    store = StagingStore(root, max_used_percent=100, max_bytes=100)
    assert store.used_bytes() == 0
    (root / "orig").mkdir(parents=True)
    (root / "orig" / "late.png").write_bytes(b"y" * 150)  # записал другой процесс (воркер)
    assert store.used_bytes() == 0
    monkeypatch.setattr(staging_module, "SIZE_TTL_SECONDS", 0.0)
    assert store.used_bytes() == 150 and not store.has_room()


def s3_client() -> S3Client:
    return S3Client(
        endpoint_url="https://s3.test.invalid",
        tenant_id="tenant",
        key_id="key",
        secret_key="secret",
        bucket_name="screens",
    )


def test_presigned_urls_carry_response_headers_and_old_calls_still_work() -> None:
    client = s3_client()
    store = S3BlobStore(client)
    original = store.presigned_get(
        "dbs-screenshots/orig/ab/x.png", expires_in=300, content_type="image/png", attachment=True
    )
    assert "response-content-disposition=attachment" in original and "response-content-type=image%2Fpng" in original
    assert "X-Amz-Expires=300" in original
    thumb = store.presigned_get(
        "dbs-screenshots/thumb/ab/x.jpg", expires_in=300, content_type="image/jpeg", attachment=False
    )
    assert "response-content-disposition=inline" in thumb
    assert "response-content" not in client.generate_presigned_url("plain/key")


async def test_s3_store_reports_a_missing_object_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    client = s3_client()
    store = S3BlobStore(client)

    async def missing(key: str) -> dict:
        raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")

    async def broken(key: str) -> dict:
        raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")

    monkeypatch.setattr(client, "head_object", missing)
    assert await store.size("k") is None
    monkeypatch.setattr(client, "head_object", broken)
    with pytest.raises(ClientError):
        await store.size("k")


def application(tmp_path: Path, *, s3: bool) -> ScreenshotControlWorkerApplication:
    settings = settings_for(tmp_path)
    settings = replace(settings, kafka=replace(settings.kafka, enabled=True))
    if s3:
        settings = replace(
            settings, s3=replace(settings.s3, enabled=True, bucket="shared", endpoint_url="https://s3.test.invalid")
        )
    return ScreenshotControlWorkerApplication(settings)


def test_storage_subscribers_start_only_with_s3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring, "build_extractor", lambda settings: FakeExtractor())
    monkeypatch.setattr(wiring, "build_decider", lambda settings: FakeDecider())

    without = application(tmp_path, s3=False)
    assert without.worker.services.blobs is None
    assert set(without.consumers()) == {"screenshot-extract", "screenshot-decide"}

    with_s3 = application(tmp_path, s3=True)
    assert with_s3.worker.services.bucket == "shared"
    assert set(with_s3.consumers()) == {
        "screenshot-extract",
        "screenshot-decide",
        "screenshot-s3-upload",
        "screenshot-s3-delete",
    }


def test_missing_recognition_is_a_clear_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring, "EXTRACTOR_FACTORY", "backend.modules.wb_screenshot_control.application.absent:build")
    monkeypatch.setattr(wiring, "DECIDE_FUNCTION", "backend.modules.wb_screenshot_control.domain.absent:decide")
    with pytest.raises(wiring.ScreenshotControlConfigError, match="application.absent"):
        wiring.build_extractor(BASE)
    with pytest.raises(wiring.ScreenshotControlConfigError, match="domain.absent"):
        wiring.build_decider(BASE)


def test_the_decider_seam_wraps_a_plain_function(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDecider(verdict="needs_review")
    monkeypatch.setattr(wiring, "_load", lambda target: lambda inp, candidates, cfg: fake.decide(inp, candidates))
    decider = wiring.build_decider(BASE)
    assert decider.decide(None, []).verdict == "needs_review"  # type: ignore[arg-type]
