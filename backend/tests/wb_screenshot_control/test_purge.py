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
from backend.modules.wb_screenshot_control.application.purge import purge_expired, purge_texts
from backend.modules.wb_screenshot_control.application.serialization import fields_from_json
from backend.modules.wb_screenshot_control.application.storage import ObjectStorageService
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
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

    async def purge_texts(*args: object) -> dict:
        steps.append("purge_texts")
        return {}

    monkeypatch.setattr(worker_module, "expire_outcomes", expire)
    monkeypatch.setattr(worker_module, "purge_expired", purge)
    monkeypatch.setattr(worker_module, "purge_texts", purge_texts)
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
    assert steps[-3:] == ["expire", "purge", "purge_texts"]
    clock["now"] = datetime(2026, 10, 23, 5, 12, tzinfo=MOSCOW)
    await worker.tick()
    assert steps[-2:] == ["expire", "reconcile"]  # чистка раз в сутки
    clock["now"] = datetime(2026, 10, 24, 4, 10, tzinfo=MOSCOW)
    await worker.tick()
    assert steps[-1] == "purge_texts"

    # без S3 файлы чистить негде, а тексты в базе — по сроку всё равно
    without_s3 = ScreenshotControlWorker(
        database, settings, services=ScreenshotControlServices(database, settings), now=lambda: clock["now"]
    )
    monkeypatch.setattr(without_s3.services, "reconciler", Reconciler)
    clock["now"] = datetime(2026, 10, 25, 4, 10, tzinfo=MOSCOW)
    steps.clear()
    await without_s3.tick()
    assert without_s3.services.blobs is None and steps == ["expire", "reconcile", "purge_texts"]


def test_fields_retention_must_outlive_the_dedupe_window() -> None:
    broken = replace(BASE, screenshot_control=replace(CONFIG, retention_fields_days=400, dedupe_window_days=400))
    with pytest.raises(ValueError, match="dedupe_window_days"):
        broken.validate_values()
    assert CONFIG.retention_fields_days == 1095 and CONFIG.dedupe_window_days == 90


async def test_texts_older_than_the_retention_are_erased_and_counts_stay(database: Database, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old_at = now - timedelta(days=CONFIG.retention_fields_days + 1)
    fresh_at = now - timedelta(days=10)
    fields = {
        "layout": "mobile_app",
        "is_wb_seller_chat": True,
        "review_text": "Размер маломерит, вернула",
        "text_norm": "размер маломерит вернула",
        "stars": 2,
        "chat_id": "c" * 36,
        "buyer_hmac": "h" * 16,
        "messages": [{"text_norm": "когда вернете деньги", "hour": 10, "minute": 5, "side": "client"}],
    }
    ids = {}
    for name, received_at in (("old", old_at), ("fresh", fresh_at)):
        async with database.session() as session:
            file = FileModel(sha256=(name * 64)[:64], size_bytes=10, mime="image/png", width=800, height=600)
            session.add(file)
            await session.flush()
            session.add(
                ExtractionModel(
                    file_id=file.id,
                    pipeline_version="v1",
                    status="ok",
                    fields=fields,
                    vlm_json={"review": {"text": "Размер маломерит"}},
                    ocr_json={"text": "Размер маломерит"},
                )
            )
            submission = SubmissionModel(
                user_id=uuid.uuid4(),
                channel="web",
                client_submission_id=name,
                file_id=file.id,
                received_at=received_at,
                stage="decided",
                verdict="accepted",
                outcome="positive",
                caption_raw="полож",
                text_norm="размер маломерит вернула",
                text_len=24,
                text_key="k" * 40,
                text_minhash=[1, 2, 3],
                chat_id_norm="c" * 36,
            )
            session.add(submission)
            await session.commit()
            ids[name] = submission.id

    first = await purge_texts(database, CONFIG, now)
    again = await purge_texts(database, CONFIG, now)

    assert first == {"submissions": 1, "extractions": 1} and again == {"submissions": 0, "extractions": 0}
    async with database.session() as session:
        old = await session.get(SubmissionModel, ids["old"])
        fresh = await session.get(SubmissionModel, ids["fresh"])
        extractions = {row.file_id: row for row in await session.scalars(select(ExtractionModel))}
    assert old is not None and fresh is not None
    assert (old.text_norm, old.caption_raw, old.text_minhash) == (None, None, None)
    assert (old.verdict, old.outcome, old.text_key, old.text_len, old.chat_id_norm) == (
        "accepted",
        "positive",
        "k" * 40,
        24,
        "c" * 36,
    )
    assert fresh.text_norm and fresh.caption_raw == "полож" and fresh.text_minhash == [1, 2, 3]
    erased = extractions[old.file_id]
    assert erased.vlm_json is None and erased.ocr_json is None and erased.fields is not None
    assert erased.fields["text_norm"] is None and erased.fields["review_text"] is None
    assert erased.fields["messages"][0]["text_norm"] == "" and erased.fields["texts_purged"] is True
    assert erased.fields["stars"] == 2 and erased.fields["buyer_hmac"] == "h" * 16
    assert fields_from_json(erased.fields).text_norm is None  # решение по старым полям всё ещё читается
    kept = extractions[fresh.file_id]
    assert kept.vlm_json and kept.fields and kept.fields["text_norm"]
