"""События конвейера и хранилища. Байтов, токенов и ключей доступа в событиях нет.

Все уходят через общий outbox (`wb_core.outbox_events`) в одной транзакции с изменением, ради
которого шлются; `event_id` outbox дописывает сам, консьюмер отсекает повтор по `inbox_events`.
"""

import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Решения принимаются по одному в порядке приёма: все `extracted` идут одним ключом партиции.
DECIDE_PARTITION_KEY = uuid.uuid5(uuid.NAMESPACE_URL, "wb-screenshot-control:decide")

EVENT_SUBMITTED = "ScreenshotSubmitted"
EVENT_EXTRACTED = "ScreenshotExtracted"
EVENT_VERDICT_READY = "ScreenshotVerdictReady"
EVENT_UPLOAD = "ScreenshotObjectUploadRequested"
EVENT_DELETE = "ScreenshotObjectDeleteRequested"


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: uuid.UUID | None = None

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"event_id"})


class ScreenshotSubmitted(_Event):
    """Скрин принят: файл в staging, строка в `submissions`. Повтор распознавания — новым событием с attempt+1."""

    submission_id: uuid.UUID
    file_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel: Literal["extension", "telegram", "web"]
    received_at: datetime
    attempt: int = Field(default=0, ge=0)
    not_before: datetime | None = None


class ScreenshotExtracted(_Event):
    submission_id: uuid.UUID
    file_id: uuid.UUID
    extraction_id: uuid.UUID
    pipeline_version: str = Field(max_length=16)
    status: Literal["ok", "failed"]


class ScreenshotVerdictReady(_Event):
    submission_id: uuid.UUID
    user_id: uuid.UUID
    channel: Literal["extension", "telegram", "web"]
    verdict: Literal["accepted", "duplicate", "needs_review", "rejected"]
    previous_verdict: Literal["accepted", "duplicate", "needs_review", "rejected"] | None
    actor: Literal["system", "admin"]
    decided_at: datetime


class _ObjectEvent(_Event):
    file_id: uuid.UUID
    kind: Literal["orig", "thumb"]
    bucket: str = Field(min_length=3, max_length=63)
    key: str = Field(min_length=1, max_length=200)

    @field_validator("key")
    @classmethod
    def key_inside_module_root(cls, value: str) -> str:
        if not value.startswith("dbs-screenshots/") or ".." in value.split("/"):
            raise ValueError("key must live under dbs-screenshots/")
        return value


class ScreenshotObjectUpload(_ObjectEvent):
    """`storage.s3.wb-screenshot-control.upload`: подписчик читает файл из staging, а не из события."""

    staging_path: str = Field(min_length=1, max_length=200, description="Путь относительно staging_root")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(gt=0)

    @field_validator("staging_path")
    @classmethod
    def staging_path_stays_inside(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("staging_path must be relative to staging_root")
        return value


class ScreenshotObjectDelete(_ObjectEvent):
    """`storage.s3.wb-screenshot-control.delete`: по сроку хранения или по решению админа."""

    reason: Literal["retention", "admin"]
