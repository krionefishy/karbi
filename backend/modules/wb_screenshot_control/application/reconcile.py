"""Сверка хранилища: зависшие выгрузки и удаления публикуются снова, пропавшие из S3 объекты
помечаются `failed`, осиротевшие файлы staging удаляются.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.storage import MAX_UPLOAD_ATTEMPTS, delete_event, upload_event
from backend.modules.wb_screenshot_control.domain.ports import BlobStore, Staging
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig
from backend.storage.pg import Database

BATCH = 200
VERIFY_BATCH = 100
ORPHAN_AGE = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    republished: int = 0
    failed: int = 0
    verified: int = 0
    missing: int = 0
    orphans_removed: int = 0
    oldest_staged_minutes: float | None = None


class StorageReconciler:
    """`blobs=None` — S3 не настроен: ничего не публикуем и не проверяем, только смотрим на staging."""

    def __init__(
        self, database: Database, staging: Staging, blobs: BlobStore | None, config: ScreenshotControlConfig
    ) -> None:
        self.database = database
        self.staging = staging
        self.blobs = blobs
        self.config = config
        self.logger = logging.getLogger("wb.screenshot_control.reconcile")

    async def run(self, now: datetime) -> ReconcileReport:
        republished, failed = await self.republish_stale(now)
        verified, missing = await self.verify_stored(now)
        orphans = await self.sweep_staging()
        oldest = await self.oldest_staged_minutes(now)
        report = ReconcileReport(republished, failed, verified, missing, orphans, oldest)
        if oldest is not None and oldest > self.config.staging_stale_minutes:
            self.logger.warning("screenshot_staging_stale", extra={"oldest_minutes": round(oldest)})
        return report

    async def republish_stale(self, now: datetime) -> tuple[int, int]:
        if self.blobs is None:
            return 0, 0
        bucket = self.blobs.bucket
        before = now - timedelta(minutes=self.config.staging_stale_minutes)
        republished = failed = 0
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            for stored in await repository.stale_objects(("staged", "uploading"), before, BATCH):
                stored.updated_at = now
                if stored.attempts >= MAX_UPLOAD_ATTEMPTS:
                    stored.state = "failed"
                    stored.last_error = stored.last_error or "upload attempts exhausted"
                    failed += 1
                    continue
                stored.bucket = bucket
                publish(session, upload_event(stored, bucket))
                republished += 1
            for stored in await repository.stale_objects(("delete_requested",), before, BATCH):
                stored.updated_at = now
                publish(session, delete_event(stored, "retention"))
                republished += 1
            await session.commit()
        if failed:
            self.logger.error("screenshot_upload_exhausted", extra={"objects": failed})
        return republished, failed

    async def verify_stored(self, now: datetime) -> tuple[int, int]:
        """Выгруженные объекты по кругу: `head_object` 404 — объекта нет, строка → `failed`."""
        if self.blobs is None:
            return 0, 0
        verified = missing = 0
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            for stored in await repository.stored_to_verify(VERIFY_BATCH):
                try:
                    size = await self.blobs.size(stored.s3_key)
                except Exception:
                    self.logger.exception("screenshot_verify_unavailable")
                    break
                stored.updated_at = now
                verified += 1
                if size is None or size != stored.size_bytes:
                    stored.state = "failed"
                    stored.last_error = "object is missing in S3" if size is None else f"S3 reports {size} bytes"
                    missing += 1
            await session.commit()
        if missing:
            self.logger.error("screenshot_objects_missing", extra={"objects": missing})
        return verified, missing

    async def sweep_staging(self) -> int:
        """Файлы staging без живой строки (упал приём до коммита, миниатюра не понадобилась) старше суток."""
        old = await self.staging.older_than(ORPHAN_AGE.total_seconds())
        if not old:
            return 0
        async with self.database.session() as session:
            live = await ScreenshotRepository(session).live_staging_paths()
        removed = 0
        for path, _age in old:
            if path not in live:
                await self.staging.delete(path)
                removed += 1
        return removed

    async def oldest_staged_minutes(self, now: datetime) -> float | None:
        async with self.database.session() as session:
            oldest = await ScreenshotRepository(session).oldest_staged_at()
        return None if oldest is None else (now - oldest).total_seconds() / 60
