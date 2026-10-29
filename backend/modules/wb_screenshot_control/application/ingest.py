"""Приём скриншота: проверка картинки, файл в staging и одна транзакция со строками и событиями.

Хеши для похожих картинок и миниатюру считает воркер; здесь только то, без чего нельзя принять.
"""

import asyncio
import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.events import ScreenshotObjectUpload, ScreenshotSubmitted
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.verdicts import decide_system, reason
from backend.modules.wb_screenshot_control.domain import (
    CHANNELS,
    OUTCOME_SOURCES,
    OUTCOMES,
    STAGE_PROCESSING,
    object_key,
    sniff_image_type,
)
from backend.modules.wb_screenshot_control.domain.ports import Staging
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import StorageObjectModel, SubmissionModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig

# Восстановить объект, который уже ушёл или не дошёл до S3: файл прислали снова.
RESTAGE_STATES = ("delete_requested", "deleted", "failed")
MAX_SIDE = 32_767  # smallint в files.width/height
_PILLOW_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
# Полное декодирование держит кадр в памяти api целиком (25 Мпикс RGBA — 100 МБ): одно за раз на процесс,
# остальные ждут в своих потоках. При `mem_limit 512m` у api два параллельных кадра — уже риск OOM всего API.
DECODE_SLOTS = 1
_DECODE_SLOTS = threading.BoundedSemaphore(DECODE_SLOTS)
_EXIF_SOFTWARE = 0x0131


class IngestError(Exception):
    """`code` — значение `ErrorCode` контракта; HTTP-статус выбирает слой presentation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ImageInfo:
    content_type: str
    width: int
    height: int
    meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IngestResult:
    submission_id: uuid.UUID
    file_id: uuid.UUID
    sha256: str
    created: bool
    channel: str
    received_at: datetime
    outcome: str | None
    stage: str
    verdict: str | None
    duplicate_of: uuid.UUID | None


def staging_path(key: str) -> str:
    """Путь в staging повторяет ключ S3 без общего префикса."""
    return key.split("/", 1)[1]


def inspect_image(data: bytes, content_type: str, *, max_pixels: int, min_side: int) -> ImageInfo:
    """Размер по заголовку до декодирования, затем полное декодирование: битый файл не принимаем."""
    with _DECODE_SLOTS:
        try:
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if width * height > max_pixels or max(width, height) > MAX_SIDE:
                    raise IngestError("file_too_large", "Слишком большое разрешение картинки")
                if min(width, height) < min_side:
                    raise IngestError("too_small", f"Скриншот меньше {min_side} px по стороне")
                if _PILLOW_FORMATS.get(image.format or "") != content_type:
                    raise IngestError("bad_image", "Файл не открывается как картинка")
                exif = image.getexif()
                meta = {
                    "format": image.format,
                    "mode": image.mode,
                    "has_alpha": "A" in image.mode or "transparency" in image.info,
                    "has_exif": bool(exif),
                    "exif_software": str(exif.get(_EXIF_SOFTWARE))[:100] if exif.get(_EXIF_SOFTWARE) else None,
                }
                image.load()
        except IngestError:
            raise
        except Image.DecompressionBombError as error:
            raise IngestError("file_too_large", "Слишком большое разрешение картинки") from error
        except (OSError, SyntaxError, ValueError) as error:
            raise IngestError("bad_image", "Файл не открывается как картинка") from error
    return ImageInfo(content_type=content_type, width=width, height=height, meta=meta)


class IngestService:
    def __init__(
        self,
        session: AsyncSession,
        staging: Staging,
        config: ScreenshotControlConfig,
        *,
        bucket: str,
    ) -> None:
        self.session = session
        self.repository = ScreenshotRepository(session)
        self.staging = staging
        self.config = config
        self.bucket = bucket

    async def ingest(
        self,
        user_id: uuid.UUID,
        channel: str,
        data: bytes,
        *,
        client_submission_id: str,
        outcome: str | None = None,
        outcome_source: str | None = None,
        client_url: str | None = None,
        client_captured_at: datetime | None = None,
        tz_offset_min: int | None = None,
        client_meta: dict[str, Any] | None = None,
        caption_raw: str | None = None,
        install_id: uuid.UUID | None = None,
    ) -> IngestResult:
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel: {channel!r}")
        if outcome is not None and outcome not in OUTCOMES:
            raise IngestError("bad_outcome", "Неизвестный исход")
        if outcome is not None and outcome_source not in OUTCOME_SOURCES:
            raise IngestError("bad_outcome", "Не указано, откуда исход")
        if len(data) > self.config.max_upload_bytes:
            raise IngestError("file_too_large", f"Файл больше {self.config.max_upload_bytes // (1024 * 1024)} МБ")
        content_type = sniff_image_type(data[:16])
        if content_type is None:
            raise IngestError("unsupported_type", "Нужен скриншот в PNG, JPEG или WEBP")
        info = await asyncio.to_thread(
            inspect_image,
            data,
            content_type,
            max_pixels=self.config.max_image_pixels,
            min_side=self.config.min_side_px,
        )
        sha256 = hashlib.sha256(data).hexdigest()

        existing = await self.repository.submission_by_client_id(user_id, client_submission_id)
        if existing is not None:
            return self._repeat(*existing, sha256)

        values = {
            "user_id": user_id,
            "channel": channel,
            "client_submission_id": client_submission_id,
            "install_id": install_id,
            "client_url": client_url[:2048] if client_url else None,
            "client_captured_at": client_captured_at,
            "client_tz_offset_min": tz_offset_min,
            "client_meta": client_meta or {},
            "outcome": outcome,
            "outcome_source": outcome_source if outcome else None,
            "caption_raw": caption_raw[:500] if caption_raw else None,
            "outcome_set_at": datetime.now(UTC) if outcome else None,
            "stage": STAGE_PROCESSING,
        }
        try:
            result = await self._store(data, sha256, info, values)
        except BaseException:
            await self.session.rollback()
            raise
        if result is None:
            # тот же client_submission_id вставлен параллельно и уже закоммичен
            await self.session.rollback()
            existing = await self.repository.submission_by_client_id(user_id, client_submission_id)
            assert existing is not None
            return self._repeat(*existing, sha256)
        await self.session.commit()
        return result

    async def _store(self, data: bytes, sha256: str, info: ImageInfo, values: dict[str, Any]) -> IngestResult | None:
        file_id = await self.repository.insert_file(
            {
                "id": uuid.uuid4(),
                "sha256": sha256,
                "size_bytes": len(data),
                "mime": info.content_type,
                "width": info.width,
                "height": info.height,
                "meta": info.meta,
            }
        )
        new_file = file_id is not None
        if file_id is None:
            known = await self.repository.file_by_sha(sha256)
            assert known is not None
            file_id = known.id

        key = object_key(sha256, "orig", info.content_type)
        path = staging_path(key)
        stored = await self.repository.storage_object(file_id, "orig", for_update=True)
        restage = stored is None or stored.state in RESTAGE_STATES
        if restage:
            if not await asyncio.to_thread(self.staging.has_room):
                raise IngestError("storage_full", "Хранилище временно переполнено, повторите позже")
            await self.staging.write(path, data)

        original = None if new_file else await self.repository.exact_original(file_id)
        submission = await self.repository.insert_submission({"id": uuid.uuid4(), "file_id": file_id, **values})
        if submission is None:
            return None

        upload = False
        if stored is None:
            upload = await self.repository.insert_storage_object(
                {
                    "file_id": file_id,
                    "kind": "orig",
                    "bucket": self.bucket,
                    "s3_key": key,
                    "staging_path": path,
                    "content_type": info.content_type,
                    "size_bytes": len(data),
                    "sha256": sha256,
                    "state": "staged",
                }
            )
        elif restage:
            self._restage(stored, path)
            upload = True
        if upload and self.bucket:
            publish(
                self.session,
                ScreenshotObjectUpload(
                    file_id=file_id,
                    kind="orig",
                    bucket=self.bucket,
                    key=key,
                    staging_path=path,
                    sha256=sha256,
                    content_type=info.content_type,  # type: ignore[arg-type]
                    size_bytes=len(data),
                ),
            )

        if original is not None:
            self._exact_duplicate(submission, original)
        else:
            publish(
                self.session,
                ScreenshotSubmitted(
                    submission_id=submission.id,
                    file_id=file_id,
                    sha256=sha256,
                    channel=submission.channel,  # type: ignore[arg-type]
                    received_at=submission.received_at,
                ),
            )
        return self._result(submission, sha256, created=True)

    def _restage(self, stored: StorageObjectModel, path: str) -> None:
        stored.state = "staged"
        stored.staging_path = path
        stored.bucket = self.bucket or stored.bucket
        stored.attempts = 0
        stored.last_error = None
        stored.stored_at = None
        stored.delete_requested_at = None
        stored.deleted_at = None
        stored.updated_at = datetime.now(UTC)

    def _exact_duplicate(self, submission: SubmissionModel, original: SubmissionModel) -> None:
        """Тот же файл уже у не отклонённого скрина: дубль без распознавания."""
        other = str(original.id)
        reasons = [reason("exact_file", "warn", other_submission_id=other)]
        if original.user_id != submission.user_id:
            reasons.append(reason("cross_user_duplicate", "high", other_submission_id=other))
        decide_system(
            self.session,
            submission,
            "duplicate",
            reasons,
            now=datetime.now(UTC),
            matches=[{"other_submission_id": other, "kind": "exact_file", "score": 1.0, "details": {}}],
            duplicate_of=original.duplicate_of or original.id,
        )

    def _repeat(self, submission: SubmissionModel, known_sha256: str, sha256: str) -> IngestResult:
        if known_sha256 != sha256:
            raise IngestError("idempotency_conflict", "Этот client_submission_id уже использован для другого файла")
        return self._result(submission, sha256, created=False)

    @staticmethod
    def _result(submission: SubmissionModel, sha256: str, *, created: bool) -> IngestResult:
        return IngestResult(
            submission_id=submission.id,
            file_id=submission.file_id,
            sha256=sha256,
            created=created,
            channel=submission.channel,
            received_at=submission.received_at,
            outcome=submission.outcome,
            stage=submission.stage,
            verdict=submission.verdict,
            duplicate_of=submission.duplicate_of,
        )
