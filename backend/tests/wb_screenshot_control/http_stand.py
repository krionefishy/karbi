"""Стенд для HTTP-тестов: приложение на PostgreSQL и Redis из `deploy/compose.test.yaml`, S3 — фейк в памяти.

Вердикты конвейера тесты пишут сами (`decide`), как это сделал бы консьюмер решения: ручкам
важно состояние строк, а не то, кто его записал. Сквозной путь через консьюмеры — отдельный тест.
"""

import io
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient, Response
from PIL import Image
from sqlalchemy import delete, select, text, update

from backend.app.factory import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.modules.wb_screenshot_control.application.pipeline import case_keys
from backend.modules.wb_screenshot_control.application.serialization import fields_to_json
from backend.modules.wb_screenshot_control.application.verdicts import decide_system
from backend.modules.wb_screenshot_control.domain import object_key
from backend.modules.wb_screenshot_control.domain.pipeline_types import ExtractedFields
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    FileModel,
    StorageObjectModel,
    SubmissionModel,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.storage.pg import Database
from backend.tests.wb_screenshot_control.fakes import MemoryBlobStore, reset, screen, settings_for

API = "/api/v1"
MANAGER = f"{API}/wb/screenshot-control"
EXTENSION = f"{MANAGER}/extension"
ADMIN = f"{API}/admin/screenshot-control"
PASSWORD = "correct horse battery staple"
BUCKET = "screens-test"
EXTRA_TABLES = ("extension_installs", "pairing_codes", "telegram_links", "telegram_link_tokens")
REDIS_PATTERNS = ("rate_limit:sc-pair:*", "sc:user:*", "rate_limit:ip-screenshots:*")


@dataclass
class RecordingBlobStore(MemoryBlobStore):
    """Помнит, какие ссылки выдавались: тип и вложение проверяются по ним."""

    signed: list[dict[str, Any]] = field(default_factory=list)

    def presigned_get(self, key: str, *, expires_in: int, content_type: str, attachment: bool) -> str:
        self.signed.append({"key": key, "ttl": expires_in, "content_type": content_type, "attachment": attachment})
        return super().presigned_get(key, expires_in=expires_in, content_type=content_type, attachment=attachment)


@dataclass
class Stand:
    client: AsyncClient
    application: Application
    prefix: str
    users: dict[str, uuid.UUID]
    blobs: RecordingBlobStore | None
    staging: Path

    @property
    def database(self) -> Database:
        return self.application.database

    async def sign_in(self, role: str) -> dict[str, str]:
        response = await self.client.post(
            f"{API}/auth/login", json={"username": f"{self.prefix}-{role}", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    async def upload(
        self,
        headers: dict[str, str],
        data: bytes | None = None,
        *,
        seed: int = 0,
        outcome: str | None = "positive",
        client_id: str | None = None,
    ) -> Response:
        form = {"client_submission_id": client_id or str(uuid.uuid4())}
        if outcome is not None:
            form["outcome"] = outcome
        payload = data if data is not None else screen(seed, width=800, height=600)
        return await self.client.post(
            f"{MANAGER}/submissions", data=form, files={"file": ("shot.png", payload, "image/png")}, headers=headers
        )

    async def submission(self, submission_id: uuid.UUID | str) -> SubmissionModel:
        async with self.database.session() as session:
            found = await session.get(SubmissionModel, uuid.UUID(str(submission_id)))
            assert found is not None
            return found

    async def decide(
        self,
        submission_id: uuid.UUID | str,
        verdict: str,
        *,
        reasons: Sequence[dict[str, Any]] = (),
        matches: Sequence[dict[str, Any]] = (),
        duplicate_of: uuid.UUID | None = None,
        fields: ExtractedFields | None = None,
        audit: bool = False,
    ) -> None:
        """Как консьюмер решения: распознавание файла, ключи кейса и вердикт с журналом и событием."""
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            submission = await repository.submission(uuid.UUID(str(submission_id)), for_update=True)
            assert submission is not None
            if fields is not None:
                await repository.upsert_extraction(
                    {
                        "file_id": submission.file_id,
                        "pipeline_version": "v1",
                        "is_current": True,
                        "status": "ok",
                        "layout": fields.layout,
                        "vlm_model": "fake-vlm",
                        "vlm_prompt": "v1",
                        "vlm_json": {"chat_id": {"value": fields.chat_id, "confidence": 0.9}},
                        "fields": fields_to_json(fields),
                        "agreement": {"chat_id": 1.0, "time": 0.95, "text": 0.9},
                        "tokens_in": 1000,
                        "tokens_out": 200,
                        "latency_ms": 900,
                    },
                    keep_ok=False,
                )
                for name, value in case_keys(fields).items():
                    setattr(submission, name, value)
            decide_system(
                session,
                submission,
                verdict,
                list(reasons),
                now=datetime.now(UTC),
                matches=list(matches),
                duplicate_of=duplicate_of,
                audit_requested=audit,
            )
            await session.commit()

    async def recognised(self, submission_id: uuid.UUID | str, fields: ExtractedFields) -> None:
        """Как консьюмер распознавания: распознано, ключи кейса записаны, решения ещё нет."""
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            submission = await repository.submission(uuid.UUID(str(submission_id)), for_update=True)
            assert submission is not None
            await repository.upsert_extraction(
                {
                    "file_id": submission.file_id,
                    "pipeline_version": "v1",
                    "is_current": True,
                    "status": "ok",
                    "layout": fields.layout,
                    "fields": fields_to_json(fields),
                },
                keep_ok=False,
            )
            for name, value in case_keys(fields).items():
                setattr(submission, name, value)
            submission.stage = "extracted"
            await session.commit()

    async def store(self, submission_id: uuid.UUID | str, *kinds: str) -> None:
        """Объекты файла выгружены в S3 (как после подписчика upload)."""
        submission = await self.submission(submission_id)
        async with self.database.session() as session:
            file = await session.get(FileModel, submission.file_id)
            assert file is not None
            for kind in kinds or ("orig", "thumb"):
                key = object_key(file.sha256, kind, file.mime)
                current = await session.scalar(
                    select(StorageObjectModel).where(
                        StorageObjectModel.file_id == file.id, StorageObjectModel.kind == kind
                    )
                )
                if current is None:
                    current = StorageObjectModel(
                        file_id=file.id,
                        kind=kind,
                        bucket=BUCKET,
                        s3_key=key,
                        content_type=file.mime if kind == "orig" else "image/jpeg",
                        size_bytes=file.size_bytes,
                        sha256=file.sha256,
                        state="stored",
                    )
                    session.add(current)
                current.bucket = BUCKET
                current.state = "stored"
                current.staging_path = None
                current.stored_at = datetime.now(UTC)
            await session.commit()

    async def set_received(self, submission_id: uuid.UUID | str, received_at: datetime) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(SubmissionModel)
                .where(SubmissionModel.id == uuid.UUID(str(submission_id)))
                .values(received_at=received_at)
            )
            await session.commit()

    async def outbox(self, event_type: str) -> list[dict[str, Any]]:
        async with self.database.session() as session:
            rows = await session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.event_type == event_type)
                .order_by(OutboxEventModel.created_at)
            )
            return [row.payload for row in rows]


def synthetic_png(width: int = 800, height: int = 600, shade: int = 240) -> bytes:
    """Синтетическая картинка: реальных скриншотов в репозитории нет и быть не должно."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (shade, shade, 245)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _clean(application: Application) -> None:
    await reset(application.database)
    async with application.database.session() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(f'wb_screenshot_control.{name}' for name in EXTRA_TABLES)} CASCADE")
        )
        await session.commit()
    for pattern in REDIS_PATTERNS:
        if stale := await application.redis.scan_iter(pattern):
            await application.redis.delete(*stale)


@asynccontextmanager
async def open_stand(
    tmp_path: Path, *, s3: bool = True, roles: Sequence[str] = ("admin", "manager", "other"), **overrides: Any
) -> AsyncIterator[Stand]:
    staging = tmp_path / "staging"
    application = Application(settings_for(staging, **overrides))
    app = application.get_app()
    prefix = f"sc-test-{uuid.uuid4().hex[:8]}"
    blobs = RecordingBlobStore(bucket=BUCKET) if s3 else None
    app.state.screenshot_blobs = blobs
    async with app.router.lifespan_context(app):
        await _clean(application)
        passwords = PasswordService()
        users: dict[str, uuid.UUID] = {}
        async with application.database.session() as session:
            repository = UserRepository(session)
            for role in roles:
                user = await repository.create(f"{prefix}-{role}", passwords.hash(PASSWORD), is_admin=role == "admin")
                users[role] = user.id
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(client, application, prefix, users, blobs, staging)
            finally:
                await _clean(application)
                async with application.database.session() as session:
                    await session.execute(delete(UserModel).where(UserModel.username.startswith(prefix)))
                    await session.commit()
