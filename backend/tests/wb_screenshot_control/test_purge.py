"""Чистка по сроку и расписание воркера."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from backend.modules.wb_screenshot_control.application.events import EVENT_DELETE, EVENT_UPLOAD
from backend.modules.wb_screenshot_control.application.ingest import IngestService
from backend.modules.wb_screenshot_control.application.purge import purge_expired
from backend.modules.wb_screenshot_control.application.storage import ObjectStorageService
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    FileModel,
    StorageObjectModel,
    SubmissionModel,
)
from backend.modules.wb_screenshot_control.infrastructure.storage import StagingStore
from backend.modules.wb_screenshot_control.infrastructure.wiring import ScreenshotControlServices
from backend.storage.pg import Database
from backend.tests.wb_screenshot_control.fakes import BASE, MemoryBlobStore, reset, screen, settings_for, take
from backend.workers.wb_screenshot_control import worker as worker_module
from backend.workers.wb_screenshot_control.worker import ScreenshotControlWorker

MOSCOW = ZoneInfo("Europe/Moscow")
CONFIG = BASE.screenshot_control


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(BASE.database.url, pool_size=2, max_overflow=1)
    await reset(database)
    try:
        yield database
    finally:
        await reset(database)
        await database.disconnect()


async def stored_file(
    database: Database, staging: StagingStore, blobs: MemoryBlobStore, seed: int, *, age_days: int, verdict: str | None
) -> uuid.UUID:
    """Скрин, выгруженный в S3 вместе с миниатюрой, полученный `age_days` назад."""
    async with database.session() as session:
        result = await IngestService(session, staging, CONFIG, bucket=blobs.bucket).ingest(
            uuid.uuid4(), "web", screen(seed), client_submission_id=str(seed), outcome="positive", outcome_source="form"
        )
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel).where(StorageObjectModel.file_id == result.file_id))
        assert stored is not None
        session.add(
            StorageObjectModel(
                file_id=result.file_id,
                kind="thumb",
                bucket=blobs.bucket,
                s3_key=stored.s3_key.replace("/orig/", "/thumb/").rsplit(".", 1)[0] + ".jpg",
                content_type="image/jpeg",
                size_bytes=3,
                sha256=stored.sha256,
                state="stored",
                stored_at=datetime.now(UTC),
            )
        )
        await session.execute(
            update(SubmissionModel)
            .where(SubmissionModel.id == result.submission_id)
            .values(received_at=datetime.now(UTC) - timedelta(days=age_days), verdict=verdict)
        )
        await session.commit()
    for row in await take(database, EVENT_UPLOAD):
        await ObjectStorageService(database, staging, blobs).upload(row.payload)
    return result.file_id


async def states(database: Database, file_id: uuid.UUID) -> dict[str, str]:
    async with database.session() as session:
        rows = await session.scalars(select(StorageObjectModel).where(StorageObjectModel.file_id == file_id))
        return {row.kind: row.state for row in rows}


async def test_purge_requests_deletion_by_retention_once(database: Database, tmp_path: Path) -> None:
    staging = StagingStore(tmp_path, max_used_percent=99)
    blobs = MemoryBlobStore()
    config = replace(CONFIG, retention_original_days=30, retention_thumb_days=180)
    old_original = await stored_file(database, staging, blobs, 1, age_days=40, verdict="accepted")
    ancient = await stored_file(database, staging, blobs, 2, age_days=200, verdict="accepted")
    fresh = await stored_file(database, staging, blobs, 3, age_days=5, verdict="accepted")
    in_review = await stored_file(database, staging, blobs, 4, age_days=40, verdict="needs_review")
    pinned = await stored_file(database, staging, blobs, 5, age_days=200, verdict="accepted")
    async with database.session() as session:
        await session.execute(update(FileModel).where(FileModel.id == pinned).values(pinned_at=datetime.now(UTC)))
        await session.commit()

    now = datetime.now(UTC)
    assert await purge_expired(database, config, now) == {"orig": 2, "thumb": 1}
    assert await states(database, old_original) == {"orig": "delete_requested", "thumb": "stored"}
    assert await states(database, ancient) == {"orig": "delete_requested", "thumb": "delete_requested"}
    assert await states(database, fresh) == {"orig": "stored", "thumb": "stored"}
    assert await states(database, in_review) == {"orig": "stored", "thumb": "stored"}
    assert await states(database, pinned) == {"orig": "stored", "thumb": "stored"}

    assert await purge_expired(database, config, now) == {"orig": 0, "thumb": 0}  # повтор ничего не дублирует
    events = await take(database, EVENT_DELETE)
    assert len(events) == 3 and {row.payload["reason"] for row in events} == {"retention"}

    service = ObjectStorageService(database, staging, blobs)
    for row in events:
        await service.delete(row.payload)
    assert await states(database, ancient) == {"orig": "deleted", "thumb": "deleted"}
    assert len(blobs.deleted) == 3


async def test_the_worker_runs_each_step_on_its_own_schedule(
    database: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []

    async def expire(*args: object) -> int:
        steps.append("expire")
        return 0

    async def purge(*args: object) -> dict:
        steps.append("purge")
        return {}

    monkeypatch.setattr(worker_module, "expire_outcomes", expire)
    monkeypatch.setattr(worker_module, "purge_expired", purge)
    settings = settings_for(tmp_path)
    services = ScreenshotControlServices(database, settings, blobs=MemoryBlobStore())

    class Reconciler:
        async def run(self, now: datetime) -> None:
            steps.append("reconcile")

    monkeypatch.setattr(services, "reconciler", Reconciler)
    clock = {"now": datetime(2026, 10, 23, 4, 0, tzinfo=MOSCOW)}
    worker = ScreenshotControlWorker(database, settings, services=services, now=lambda: clock["now"])

    await worker.tick()
    assert steps == ["expire", "reconcile"]  # до 04:10 чистки нет
    clock["now"] += timedelta(seconds=30)
    await worker.tick()
    assert steps == ["expire", "reconcile"]
    clock["now"] = datetime(2026, 10, 23, 4, 11, tzinfo=MOSCOW)
    await worker.tick()
    assert steps[-2:] == ["expire", "purge"]
    clock["now"] = datetime(2026, 10, 23, 5, 12, tzinfo=MOSCOW)
    await worker.tick()
    assert steps[-2:] == ["expire", "reconcile"]  # чистка раз в сутки
    clock["now"] = datetime(2026, 10, 24, 4, 10, tzinfo=MOSCOW)
    await worker.tick()
    assert steps[-1] == "purge"

    without_s3 = ScreenshotControlWorker(database, settings, services=ScreenshotControlServices(database, settings))
    assert without_s3.services.blobs is None and not without_s3.purge_due(clock["now"])
