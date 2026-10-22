"""Синтетика и фейки для приёма, конвейера и хранилища. Реальных скриншотов в репозитории нет."""

import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from sqlalchemy import delete, select, text, update

from backend.modules.wb_core.infrastructure.postgres.models import InboxEventModel, OutboxEventModel
from backend.modules.wb_screenshot_control.application.events import (
    EVENT_DELETE,
    EVENT_EXTRACTED,
    EVENT_SUBMITTED,
    EVENT_UPLOAD,
    EVENT_VERDICT_READY,
)
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ClientHints,
    Decision,
    DecisionInput,
    ExtractedFields,
    ExtractionResult,
)
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database

BASE = load_settings("backend/shared/settings/config.test.yaml")
SCHEMA = "wb_screenshot_control"
EVENT_TYPES = (EVENT_SUBMITTED, EVENT_EXTRACTED, EVENT_VERDICT_READY, EVENT_UPLOAD, EVENT_DELETE)
TABLES = ("verdict_events", "storage_objects", "extractions", "submissions", "files")


def settings_for(staging: Path, **overrides: Any) -> Settings:
    screens = replace(BASE.screenshot_control, staging_root=str(staging), **overrides)
    return replace(BASE, screenshot_control=screens)


def screen(seed: int = 0, *, width: int = 1280, height: int = 800, fmt: str = "PNG", quality: int = 90) -> bytes:
    """Окно «кабинета»: шапка и панели одинаковы у всех, блоки контента зависят от `seed`."""
    rng = random.Random(seed)
    image = Image.new("RGB", (width, height), (245, 246, 248))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, height // 12), fill=(60, 30, 120))
    draw.rectangle((0, height // 12, width // 5, height), fill=(230, 230, 236))
    for _ in range(14):
        left = rng.randrange(width // 5, width - 60)
        top = rng.randrange(height // 10, height - 40)
        shade = rng.randrange(0, 256)
        draw.rectangle(
            (left, top, min(width, left + rng.randrange(60, width // 2)), min(height, top + rng.randrange(20, 200))),
            fill=(shade, 255 - shade, rng.randrange(0, 256)),
        )
    draw.text((width // 4, height // 6), f"Отзыв {seed}", fill=(0, 0, 0))
    return encode(image, fmt, quality)


def encode(image: Image.Image, fmt: str = "PNG", quality: int = 90) -> bytes:
    buffer = BytesIO()
    options = {} if fmt == "PNG" else {"quality": quality}
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def desktop_fields(
    chat_id: str | None = None, text_norm: str = "товар пришел сломанный верните деньги пожалуйста"
) -> ExtractedFields:
    return ExtractedFields(
        layout="desktop_window",
        is_wb_seller_chat=True,
        chat_id=chat_id or str(uuid.uuid4()),
        chat_id_source="image",
        review_at=datetime(2026, 10, 22, 3, 39, tzinfo=UTC),
        review_time_precision="minute",
        stars=1,
        review_text=text_norm,
        text_norm=text_norm,
        article_norm="123456",
        buyer_hmac="0123456789abcdef",
        agreement={"chat_id": 1.0},
    )


def ok_result(fields: ExtractedFields | None = None) -> ExtractionResult:
    return ExtractionResult(
        status="ok",
        fields=fields or desktop_fields(),
        vlm_model="fake-vlm",
        vlm_prompt="v1",
        vlm_json={"layout": "desktop_window", "buyer_name": {"value": "Иван Петров", "confidence": 0.9}},
        tokens_in=1000,
        tokens_out=200,
        latency_ms=900,
    )


@dataclass
class FakeExtractor:
    """Отвечает по очереди из `script`: результат или исключение; последний ответ повторяется."""

    script: list[ExtractionResult | Exception] = field(default_factory=lambda: [ok_result()])
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def extract(
        self, image: bytes, *, channel: str, hints: ClientHints, received_at: datetime
    ) -> ExtractionResult:
        self.calls.append({"size": len(image), "channel": channel, "hints": hints, "received_at": received_at})
        answer = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


@dataclass
class FakeDecider:
    verdict: str = "accepted"
    calls: list[tuple[DecisionInput, list[Candidate]]] = field(default_factory=list)

    def decide(self, inp: DecisionInput, candidates: Sequence[Candidate]) -> Decision:
        self.calls.append((inp, list(candidates)))
        earlier = [item for item in candidates if item.received_at < inp.received_at]
        if self.verdict == "duplicate":
            if not earlier:
                return Decision(verdict="accepted")
            return Decision(verdict="duplicate", duplicate_of=earlier[0].submission_id)
        return Decision(verdict=self.verdict)  # type: ignore[arg-type]


@dataclass
class MemoryBlobStore:
    bucket: str = "screens-test"
    objects: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    down: bool = False
    deleted: list[str] = field(default_factory=list)

    def _check(self) -> None:
        if self.down:
            raise ConnectionError("S3 is unreachable")

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self._check()
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        self._check()
        return self.objects[key][0]

    async def size(self, key: str) -> int | None:
        self._check()
        stored = self.objects.get(key)
        return len(stored[0]) if stored else None

    async def delete(self, key: str) -> None:
        self._check()
        self.objects.pop(key, None)
        self.deleted.append(key)

    def presigned_get(self, key: str, *, expires_in: int, content_type: str, attachment: bool) -> str:
        return f"https://s3.test/{self.bucket}/{key}?expires={expires_in}"


async def reset(database: Database) -> None:
    async with database.session() as session:
        await session.execute(text(f"TRUNCATE {', '.join(f'{SCHEMA}.{name}' for name in TABLES)} CASCADE"))
        await session.execute(delete(OutboxEventModel).where(OutboxEventModel.event_type.in_(EVENT_TYPES)))
        await session.execute(delete(InboxEventModel).where(InboxEventModel.event_type.in_(EVENT_TYPES)))
        await session.commit()


async def take(database: Database, event_type: str) -> list[OutboxEventModel]:
    """Неопубликованные события типа — как их увидит консьюмер; помечаются опубликованными."""
    async with database.session() as session:
        rows = list(
            await session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.event_type == event_type, OutboxEventModel.published_at.is_(None))
                .order_by(OutboxEventModel.created_at)
            )
        )
        if rows:
            await session.execute(
                update(OutboxEventModel)
                .where(OutboxEventModel.id.in_([row.id for row in rows]))
                .values(published_at=text("now()"))
            )
            await session.commit()
    return rows
