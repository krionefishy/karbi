"""Ссылки на картинки: только presigned GET напрямую из S3 с коротким сроком.

Пока файл в staging, выгрузка не дошла или S3 не настроен, ссылки нет: `available=false`.
Ключи доступа в ссылку не попадают — только подпись S3.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.modules.wb_screenshot_control.domain.ports import BlobStore
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import StorageObjectModel


@dataclass(frozen=True, slots=True)
class ImageLinks:
    blobs: BlobStore | None
    ttl_seconds: int

    def link(self, stored: StorageObjectModel | None, *, attachment: bool = False) -> str | None:
        if self.blobs is None or stored is None or stored.state != "stored":
            return None
        return self.blobs.presigned_get(
            stored.s3_key, expires_in=self.ttl_seconds, content_type=stored.content_type, attachment=attachment
        )

    def answer(
        self, stored: StorageObjectModel | None, *, attachment: bool = False, now: datetime | None = None
    ) -> dict[str, Any]:
        url = self.link(stored, attachment=attachment)
        if url is None:
            return {"url": None, "expires_at": None, "available": False}
        moment = now or datetime.now(UTC)
        return {"url": url, "expires_at": moment + timedelta(seconds=self.ttl_seconds), "available": True}
