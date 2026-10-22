"""Подписчики `storage.s3.wb-screenshot-control.*` и чтение байтов файла.

Запись и удаление в S3 — только здесь, по событию. Неудача S3 не блокирует партицию: объект
возвращается в `staged` (или остаётся `delete_requested`) с причиной, повтор публикует сверка.
"""

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_screenshot_control.application.events import (
    EVENT_DELETE,
    EVENT_UPLOAD,
    ScreenshotObjectDelete,
    ScreenshotObjectUpload,
)
from backend.modules.wb_screenshot_control.application.inbox import parse_event
from backend.modules.wb_screenshot_control.domain.ports import BlobStore, Staging
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import StorageObjectModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.storage.pg import Database

MAX_UPLOAD_ATTEMPTS = 24
UPLOADABLE = ("staged", "uploading")


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:500]


class ObjectReader:
    """Байты файла: из staging, пока он там, иначе из S3."""

    def __init__(self, staging: Staging, blobs: BlobStore | None) -> None:
        self.staging = staging
        self.blobs = blobs

    async def read(self, stored: StorageObjectModel) -> bytes:
        if stored.staging_path:
            try:
                return await self.staging.read(stored.staging_path)
            except FileNotFoundError:
                pass
        if self.blobs is not None and stored.state in ("stored", "uploading", "delete_requested"):
            return await self.blobs.get(stored.s3_key)
        raise FileNotFoundError(f"{stored.kind} of file {stored.file_id} is neither staged nor stored")


class ObjectStorageService:
    def __init__(self, database: Database, staging: Staging, blobs: BlobStore) -> None:
        self.database = database
        self.staging = staging
        self.blobs = blobs
        self.logger = logging.getLogger("wb.screenshot_control.storage")

    async def upload(self, payload: dict[str, Any]) -> None:
        event, event_id = parse_event(ScreenshotObjectUpload, payload)
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return
            stored = await repository.storage_object(event.file_id, event.kind, for_update=True)
            leftover: str | None = None
            proceed = False
            if stored is None or stored.s3_key != event.key or event.bucket != self.blobs.bucket:
                self.logger.warning("screenshot_upload_skipped", extra={"file_id": str(event.file_id)})
            elif stored.state == "stored":
                leftover = stored.staging_path or event.staging_path
            elif stored.state in UPLOADABLE:
                stored.state = "uploading"
                stored.bucket = self.blobs.bucket
                stored.attempts += 1
                stored.updated_at = datetime.now(UTC)
                proceed = True
            if not proceed:
                repository.mark_inbox(event_id, EVENT_UPLOAD)
            await session.commit()
        if leftover:
            await self.staging.delete(leftover)
        if not proceed:
            return

        state, error = await self._put(event)
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            stored = await repository.storage_object(event.file_id, event.kind, for_update=True)
            if stored is not None and stored.state == "uploading":
                now = datetime.now(UTC)
                if state == "staged" and stored.attempts >= MAX_UPLOAD_ATTEMPTS:
                    state = "failed"
                stored.state = state
                stored.last_error = error
                stored.updated_at = now
                if state == "stored":
                    leftover = stored.staging_path
                    stored.staging_path = None
                    stored.stored_at = now
            repository.mark_inbox(event_id, EVENT_UPLOAD)
            await session.commit()
        if state == "stored" and leftover:
            await self.staging.delete(leftover)
        if error:
            self.logger.warning("screenshot_upload_failed", extra={"file_id": str(event.file_id), "error": error})

    async def _put(self, event: ScreenshotObjectUpload) -> tuple[str, str | None]:
        """Новое состояние объекта и причина неудачи."""
        try:
            data = await self.staging.read(event.staging_path)
        except FileNotFoundError:
            try:
                size = await self.blobs.size(event.key)
            except Exception as error:
                return "staged", _error(error)
            if size == event.size_bytes:
                return "stored", None
            return "failed", "staging file is lost and the object is not in S3"
        if len(data) != event.size_bytes or hashlib.sha256(data).hexdigest() != event.sha256:
            return "failed", "staging file does not match its sha256"
        try:
            await self.blobs.put(event.key, data, event.content_type)
            size = await self.blobs.size(event.key)
        except Exception as error:
            return "staged", _error(error)
        if size != event.size_bytes:
            return "staged", f"S3 reports {size} bytes instead of {event.size_bytes}"
        return "stored", None

    async def delete(self, payload: dict[str, Any]) -> None:
        event, event_id = parse_event(ScreenshotObjectDelete, payload)
        leftover: str | None = None
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return
            # Строка под блокировкой на время удаления: повторный приём того же файла дождётся
            # и вернёт объект в staged, а не потеряет его между delete и записью состояния.
            stored = await repository.storage_object(event.file_id, event.kind, for_update=True)
            if stored is not None and stored.state == "delete_requested" and stored.s3_key == event.key:
                now = datetime.now(UTC)
                try:
                    await self.blobs.delete(event.key)
                except Exception as error:
                    stored.last_error = _error(error)
                    self.logger.warning(
                        "screenshot_delete_failed", extra={"file_id": str(event.file_id), "error": stored.last_error}
                    )
                else:
                    stored.state = "deleted"
                    stored.deleted_at = now
                    stored.last_error = None
                    leftover = stored.staging_path
                    stored.staging_path = None
                stored.updated_at = now
            repository.mark_inbox(event_id, EVENT_DELETE)
            await session.commit()
        if leftover:
            await self.staging.delete(leftover)


def upload_event(stored: StorageObjectModel, bucket: str) -> ScreenshotObjectUpload:
    return ScreenshotObjectUpload(
        file_id=stored.file_id,
        kind=stored.kind,  # type: ignore[arg-type]
        bucket=bucket,
        key=stored.s3_key,
        staging_path=stored.staging_path or stored.s3_key.split("/", 1)[1],
        sha256=stored.sha256,
        content_type=stored.content_type,  # type: ignore[arg-type]
        size_bytes=stored.size_bytes,
    )


def delete_event(stored: StorageObjectModel, reason: str) -> ScreenshotObjectDelete:
    return ScreenshotObjectDelete(
        file_id=stored.file_id,
        kind=stored.kind,  # type: ignore[arg-type]
        bucket=stored.bucket,
        key=stored.s3_key,
        reason=reason,  # type: ignore[arg-type]
    )
