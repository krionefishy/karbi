"""Сборка модуля из `Settings`. Распознавание и решение подключаются швами `build_extractor` и
`build_decider`: реализации живут в своих модулях и импортируются лениво.
"""

import importlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.ingest import IngestService
from backend.modules.wb_screenshot_control.application.pipeline import DecisionService, ExtractionService
from backend.modules.wb_screenshot_control.application.reconcile import StorageReconciler
from backend.modules.wb_screenshot_control.application.storage import ObjectReader, ObjectStorageService
from backend.modules.wb_screenshot_control.domain.pipeline_types import Candidate, Decision, DecisionInput
from backend.modules.wb_screenshot_control.domain.ports import BlobStore, Decider, Extractor
from backend.modules.wb_screenshot_control.infrastructure.storage import S3BlobStore, StagingStore
from backend.shared.settings import Settings
from backend.storage.pg import Database
from backend.storage.s3 import S3Client

EXTRACTOR_FACTORY = "backend.modules.wb_screenshot_control.application.extractor:build_extractor"
DECIDE_FUNCTION = "backend.modules.wb_screenshot_control.domain.decide:decide"


class ScreenshotControlConfigError(RuntimeError):
    """Модуль собран не полностью: нет реализации распознавания или решения."""


def _load(target: str) -> Any:
    module_name, attribute = target.split(":")
    try:
        return getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as error:
        raise ScreenshotControlConfigError(f"Контроль скриншотов: нет реализации {target} ({error})") from error


def build_extractor(settings: Settings) -> Extractor:
    """`application.extractor.build_extractor(settings)` — VLM + OCR + нормализация."""
    factory = _load(EXTRACTOR_FACTORY)
    extractor: Extractor = factory(settings)
    return extractor


class _FunctionDecider:
    def __init__(self, function: Any, config: Any) -> None:
        self.function = function
        self.config = config

    def decide(self, inp: DecisionInput, candidates: Sequence[Candidate]) -> Decision:
        decision: Decision = self.function(inp, candidates, self.config)
        return decision


def build_decider(settings: Settings) -> Decider:
    """`domain.decide.decide(inp, candidates, cfg) -> Decision` — чистая функция правил уникальности."""
    return _FunctionDecider(_load(DECIDE_FUNCTION), settings.screenshot_control)


def storage_bucket(settings: Settings) -> str:
    """Бакет, в который можно выгружать сейчас; пусто — S3 выключен или не настроен, файлы копятся в staging."""
    if not settings.s3.enabled:
        return ""
    return settings.screenshot_control.bucket(settings.s3)


def build_staging(settings: Settings) -> StagingStore:
    screens = settings.screenshot_control
    return StagingStore(screens.staging_root, max_used_percent=screens.staging_max_used_percent)


def build_blob_store(settings: Settings) -> BlobStore | None:
    bucket = storage_bucket(settings)
    if not bucket:
        return None
    s3 = settings.s3
    return S3BlobStore(
        S3Client(
            endpoint_url=s3.endpoint_url,
            tenant_id=s3.tenant_id,
            key_id=s3.key_id,
            secret_key=s3.secret_key,
            bucket_name=bucket,
            region=s3.region,
            verify_tls=s3.verify_tls,
        )
    )


def build_ingest(session: AsyncSession, settings: Settings) -> IngestService:
    return IngestService(session, build_staging(settings), settings.screenshot_control, bucket=storage_bucket(settings))


class ScreenshotControlServices:
    """Всё, что нужно воркеру. Распознавание и решение собираются только для консьюмеров конвейера."""

    def __init__(self, database: Database, settings: Settings, *, blobs: BlobStore | None = None) -> None:
        self.database = database
        self.settings = settings
        self.config = settings.screenshot_control
        self.staging = build_staging(settings)
        self.blobs = blobs if blobs is not None else build_blob_store(settings)
        self.reader = ObjectReader(self.staging, self.blobs)

    @property
    def bucket(self) -> str:
        return self.blobs.bucket if self.blobs is not None else ""

    def extraction(self, extractor: Extractor | None = None) -> ExtractionService:
        return ExtractionService(
            self.database,
            self.staging,
            self.reader,
            extractor or build_extractor(self.settings),
            self.config,
            bucket=self.bucket,
        )

    def decision(self, decider: Decider | None = None) -> DecisionService:
        return DecisionService(self.database, decider or build_decider(self.settings), self.config)

    def object_storage(self) -> ObjectStorageService | None:
        if self.blobs is None:
            return None
        return ObjectStorageService(self.database, self.staging, self.blobs)

    def reconciler(self) -> StorageReconciler:
        return StorageReconciler(self.database, self.staging, self.blobs, self.config)
