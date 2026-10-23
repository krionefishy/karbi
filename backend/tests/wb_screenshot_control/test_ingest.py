"""Приём скриншота на PostgreSQL из `deploy/compose.test.yaml`; S3 не нужен — приём в него не ходит."""

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from PIL import Image, ImageFile
from sqlalchemy import func, select

from backend.modules.wb_screenshot_control.application.events import (
    EVENT_SUBMITTED,
    EVENT_UPLOAD,
    EVENT_VERDICT_READY,
)
from backend.modules.wb_screenshot_control.application.ingest import (
    DECODE_SLOTS,
    IngestError,
    IngestResult,
    IngestService,
    inspect_image,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    FileModel,
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)
from backend.modules.wb_screenshot_control.infrastructure.storage import StagingStore
from backend.modules.wb_screenshot_control.infrastructure.wiring import build_ingest, storage_bucket
from backend.storage.pg import Database
from backend.tests.wb_screenshot_control.fakes import BASE, encode, reset, screen, settings_for, take

BUCKET = "screens-test"
USER = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000002")


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(BASE.database.url, pool_size=4, max_overflow=2)
    await reset(database)
    try:
        yield database
    finally:
        await reset(database)
        await database.disconnect()


@pytest.fixture
def staging(tmp_path: Path) -> StagingStore:
    return StagingStore(tmp_path / "staging", max_used_percent=99)


def service(session, staging: StagingStore, *, bucket: str = BUCKET, **config) -> IngestService:  # noqa: ANN001
    return IngestService(session, staging, replace(BASE.screenshot_control, **config), bucket=bucket)


async def ingest(
    database: Database, staging: StagingStore, data: bytes, *, user: uuid.UUID = USER, **kwargs: Any
) -> IngestResult:
    options: dict[str, Any] = {
        "client_submission_id": str(uuid.uuid4()),
        "outcome": "positive",
        "outcome_source": "form",
    }
    options.update(kwargs)
    config = {key: options.pop(key) for key in list(options) if key in ("max_upload_bytes", "max_image_pixels")}
    bucket = options.pop("bucket", BUCKET)
    async with database.session() as session:
        return await service(session, staging, bucket=bucket, **config).ingest(user, "web", data, **options)


async def count(database: Database, model: type) -> int:
    async with database.session() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_a_screenshot_is_staged_and_queued_in_one_transaction(database: Database, staging: StagingStore) -> None:
    data = screen(1)
    result = await ingest(
        database, staging, data, client_url="https://seller.wildberries.ru/chat-with-clients?chatId=x"
    )

    assert result.created and result.stage == "processing" and result.verdict is None
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    async with database.session() as session:
        file = await session.get(FileModel, result.file_id)
        stored = await session.scalar(select(StorageObjectModel))
        submission = await session.get(SubmissionModel, result.submission_id)
    assert file is not None and (file.width, file.height, file.mime) == (1280, 800, "image/png")
    assert file.dhash is None  # хеши считает воркер
    assert stored is not None and stored.state == "staged" and stored.kind == "orig" and stored.bucket == BUCKET
    assert stored.s3_key == f"dbs-screenshots/orig/{result.sha256[:2]}/{result.sha256}.png"
    assert await staging.read(stored.staging_path or "") == data
    assert submission is not None and submission.outcome == "positive" and submission.outcome_set_at is not None

    submitted = await take(database, EVENT_SUBMITTED)
    uploads = await take(database, EVENT_UPLOAD)
    assert [row.payload["submission_id"] for row in submitted] == [str(result.submission_id)]
    assert uploads[0].payload["staging_path"] == stored.staging_path
    assert "content_base64" not in uploads[0].payload and len(str(uploads[0].payload)) < 1000  # байтов в событии нет


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
async def test_jpeg_and_webp_are_accepted(database: Database, staging: StagingStore, fmt: str) -> None:
    with Image.open(BytesIO(screen(2))) as image:
        data = encode(image, fmt)
    result = await ingest(database, staging, data)
    async with database.session() as session:
        file = await session.get(FileModel, result.file_id)
    assert file is not None and file.mime == f"image/{fmt.lower()}"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"GIF89a" + b"\x00" * 100, "unsupported_type"),
        (b"%PDF-1.7 not an image", "unsupported_type"),
        (screen(3)[:4000], "bad_image"),
        (encode(Image.new("RGB", (400, 300), "white")), "too_small"),
    ],
    ids=["gif", "pdf", "truncated_png", "too_small"],
)
async def test_bad_files_are_refused_without_a_trace(
    database: Database, staging: StagingStore, data: bytes, code: str
) -> None:
    with pytest.raises(IngestError) as refused:
        await ingest(database, staging, data)
    assert refused.value.code == code
    assert await count(database, SubmissionModel) == 0
    assert await count(database, FileModel) == 0


async def test_size_limits_are_checked_before_decoding(
    database: Database, staging: StagingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(IngestError) as too_big:
        await ingest(database, staging, screen(4), max_upload_bytes=1000)
    assert too_big.value.code == "file_too_large"

    def explode(self) -> None:  # noqa: ANN001
        raise AssertionError("a bomb must be refused before decoding")

    bomb = encode(Image.new("1", (6000, 5000)))  # 30 Мпикс, файл в несколько килобайт
    monkeypatch.setattr(ImageFile.ImageFile, "load", explode)
    with pytest.raises(IngestError) as refused:
        await ingest(database, staging, bomb, max_image_pixels=20_000_000)
    assert refused.value.code == "file_too_large"


async def test_bad_outcome_is_refused(database: Database, staging: StagingStore) -> None:
    with pytest.raises(IngestError) as refused:
        await ingest(database, staging, screen(5), outcome="maybe")
    assert refused.value.code == "bad_outcome"


async def test_a_repeated_client_id_returns_the_same_record_or_conflicts(
    database: Database, staging: StagingStore
) -> None:
    client_id = str(uuid.uuid4())
    first = await ingest(database, staging, screen(6), client_submission_id=client_id)
    again = await ingest(database, staging, screen(6), client_submission_id=client_id)
    assert again.submission_id == first.submission_id and not again.created
    assert len(await take(database, EVENT_SUBMITTED)) == 1

    with pytest.raises(IngestError) as conflict:
        await ingest(database, staging, screen(7), client_submission_id=client_id)
    assert conflict.value.code == "idempotency_conflict"
    assert await count(database, SubmissionModel) == 1


async def test_a_full_staging_refuses_with_storage_full(
    database: Database, staging: StagingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(staging, "has_room", lambda: False)
    with pytest.raises(IngestError) as full:
        await ingest(database, staging, screen(8))
    assert full.value.code == "storage_full"
    assert await count(database, FileModel) == 0
    assert await staging.older_than(0) == []


async def test_an_exact_copy_is_a_duplicate_at_once(database: Database, staging: StagingStore) -> None:
    data = screen(9)
    original = await ingest(database, staging, data)
    await take(database, EVENT_SUBMITTED)
    await take(database, EVENT_UPLOAD)

    copy = await ingest(database, staging, data, user=OTHER, outcome="amended")
    assert copy.verdict == "duplicate" and copy.stage == "decided" and copy.duplicate_of == original.submission_id
    async with database.session() as session:
        row = await session.get(SubmissionModel, copy.submission_id)
        events = list(await session.scalars(select(VerdictEventModel)))
    assert row is not None
    assert [item["code"] for item in row.reasons] == ["exact_file", "cross_user_duplicate"]
    assert row.matches[0]["kind"] == "exact_file" and row.decided_at is not None
    assert [(event.actor, event.to_verdict) for event in events] == [("system", "duplicate")]
    assert await take(database, EVENT_SUBMITTED) == []  # без распознавания
    assert await take(database, EVENT_UPLOAD) == []  # файл уже в работе
    ready = await take(database, EVENT_VERDICT_READY)
    assert ready[0].payload["verdict"] == "duplicate"

    mine = await ingest(database, staging, data)
    async with database.session() as session:
        row = await session.get(SubmissionModel, mine.submission_id)
    assert row is not None and [item["code"] for item in row.reasons] == ["exact_file"]
    assert await count(database, FileModel) == 1


async def test_a_copy_of_a_rejected_screen_goes_through_the_pipeline(database: Database, staging: StagingStore) -> None:
    data = screen(10)
    first = await ingest(database, staging, data)
    async with database.session() as session:
        row = await session.get(SubmissionModel, first.submission_id)
        assert row is not None
        row.verdict = "rejected"
        await session.commit()
    await take(database, EVENT_SUBMITTED)

    again = await ingest(database, staging, data)
    assert again.verdict is None and again.stage == "processing"
    assert len(await take(database, EVENT_SUBMITTED)) == 1


async def test_a_purged_original_is_staged_and_uploaded_again(database: Database, staging: StagingStore) -> None:
    data = screen(11)
    first = await ingest(database, staging, data)
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel))
        assert stored is not None
        path = stored.staging_path or ""
        stored.state, stored.staging_path = "deleted", None
        await session.commit()
    await staging.delete(path)
    await take(database, EVENT_UPLOAD)

    second = await ingest(database, staging, data, user=OTHER)
    assert second.verdict == "duplicate" and second.duplicate_of == first.submission_id
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel))
    assert stored is not None and stored.state == "staged" and stored.deleted_at is None
    assert await staging.read(path) == data
    uploads = await take(database, EVENT_UPLOAD)
    assert [row.payload["key"] for row in uploads] == [stored.s3_key]


async def test_without_s3_the_file_waits_in_staging_and_no_upload_is_queued(
    database: Database, staging: StagingStore, tmp_path: Path
) -> None:
    assert storage_bucket(settings_for(tmp_path)) == ""  # тестовый конфиг: S3 выключен

    result = await ingest(database, staging, screen(12), bucket="")
    async with database.session() as session:
        stored = await session.scalar(select(StorageObjectModel))
    assert result.created and stored is not None and stored.state == "staged" and stored.bucket == ""
    assert await take(database, EVENT_UPLOAD) == []
    assert len(await take(database, EVENT_SUBMITTED)) == 1


async def test_wiring_builds_the_service_from_settings(database: Database, tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "wired")
    async with database.session() as session:
        result = await build_ingest(session, settings).ingest(
            USER, "extension", screen(13), client_submission_id="ext-1", outcome="deleted", outcome_source="button"
        )
    assert result.channel == "extension"
    assert (tmp_path / "wired" / "orig" / result.sha256[:2] / f"{result.sha256}.png").is_file()


async def test_two_parallel_uploads_of_one_file_share_it(database: Database, staging: StagingStore) -> None:
    data = screen(14)
    first, second = await asyncio.gather(
        ingest(database, staging, data, user=USER), ingest(database, staging, data, user=OTHER)
    )
    assert first.file_id == second.file_id and first.submission_id != second.submission_id
    assert await count(database, FileModel) == 1
    assert await count(database, StorageObjectModel) == 1
    assert len(await take(database, EVENT_UPLOAD)) == 1
    verdicts = sorted([first.verdict or "", second.verdict or ""])
    assert verdicts == ["", "duplicate"]  # вторая транзакция дождалась первой и увидела точную копию


def test_the_default_pixel_limit_fits_real_screens_and_refuses_more() -> None:
    limit = BASE.screenshot_control.max_image_pixels
    assert limit == 25_000_000
    six_k = encode(Image.new("1", (6016, 3384)))  # Pro Display XDR — 20,4 Мпикс
    assert inspect_image(six_k, "image/png", max_pixels=limit, min_side=500).width == 6016
    with pytest.raises(IngestError) as large:
        inspect_image(encode(Image.new("1", (6000, 5000))), "image/png", max_pixels=limit, min_side=500)
    assert large.value.code == "file_too_large"


async def test_full_decodes_go_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Память api: полное декодирование — одно на процесс, параллельные приёмы ждут."""
    assert DECODE_SLOTS == 1
    active = peak = 0
    lock = threading.Lock()
    original = Image.Image.load

    def slow_load(self: Image.Image) -> Any:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        try:
            return original(self)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(Image.Image, "load", slow_load)
    images = [screen(seed) for seed in range(4)]
    await asyncio.gather(
        *(asyncio.to_thread(inspect_image, data, "image/png", max_pixels=25_000_000, min_side=500) for data in images)
    )
    assert peak == 1
