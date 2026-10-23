"""Цепочка приём → распознавание → решение на PostgreSQL с фейками распознавания, решения и S3."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest
import pytest_asyncio
from sqlalchemy import cast, select, update
from sqlalchemy.types import String

from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.events import (
    DECIDE_PARTITION_KEY,
    EVENT_EXTRACTED,
    EVENT_SUBMITTED,
    EVENT_UPLOAD,
    EVENT_VERDICT_READY,
)
from backend.modules.wb_screenshot_control.application.images import ImageLinks
from backend.modules.wb_screenshot_control.application.ingest import IngestResult
from backend.modules.wb_screenshot_control.application.outcomes import expire_outcomes
from backend.modules.wb_screenshot_control.application.pipeline import MAX_DEFERRALS, RETRY_DELAYS, case_keys
from backend.modules.wb_screenshot_control.application.submissions_service import SubmissionsService
from backend.modules.wb_screenshot_control.domain.pipeline_types import ExtractionResult
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    FileModel,
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)
from backend.modules.wb_screenshot_control.infrastructure.wiring import ScreenshotControlServices, build_ingest
from backend.storage.pg import Database
from backend.tests.wb_screenshot_control.fakes import (
    BASE,
    FakeDecider,
    FakeExtractor,
    MemoryBlobStore,
    desktop_fields,
    ok_result,
    reset,
    screen,
    settings_for,
    take,
)

USER = uuid.UUID("00000000-0000-4000-8000-000000000021")
OTHER = uuid.UUID("00000000-0000-4000-8000-000000000022")


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class Pipeline:
    database: Database
    services: ScreenshotControlServices
    extractor: FakeExtractor
    decider: FakeDecider
    clock: Clock

    async def ingest(self, seed: int, *, user: uuid.UUID = USER, outcome: str | None = "positive") -> IngestResult:
        async with self.database.session() as session:
            service = build_ingest(session, self.services.settings)
            service.bucket = self.services.bucket
            return await service.ingest(
                user,
                "web",
                screen(seed),
                client_submission_id=str(uuid.uuid4()),
                outcome=outcome,
                outcome_source="form" if outcome else None,
                client_url="https://seller.wildberries.ru/chat-with-clients?chatId=x",
            )

    async def extract(self, payload: dict[str, Any]) -> None:
        service = self.services.extraction(self.extractor)
        service.now = self.clock
        await service.handle(payload)

    async def decide(self, payload: dict[str, Any]) -> None:
        service = self.services.decision(self.decider)
        service.now = self.clock
        await service.handle(payload)

    async def choose_outcome(self, submission_id: uuid.UUID, outcome: str = "positive", user: uuid.UUID = USER) -> str:
        """Как `PATCH …/outcome`: исход от менеджера, ответ — статус карточки."""
        async with self.database.session() as session:
            service = SubmissionsService(session, self.services.config, ImageLinks(None, 300), now=self.clock)
            view = await service.change_outcome(user, submission_id, outcome)
        return str(view["status"])

    async def submission(self, submission_id: uuid.UUID) -> SubmissionModel:
        async with self.database.session() as session:
            row = await session.get(SubmissionModel, submission_id)
        assert row is not None
        return row


@pytest_asyncio.fixture
async def pipeline(tmp_path: Path) -> AsyncIterator[Pipeline]:
    database = Database()
    await database.connect(BASE.database.url, pool_size=3, max_overflow=2)
    await reset(database)
    services = ScreenshotControlServices(database, settings_for(tmp_path / "staging"), blobs=MemoryBlobStore())
    try:
        yield Pipeline(database, services, FakeExtractor(), FakeDecider(), Clock())
    finally:
        await reset(database)
        await database.disconnect()


async def one(database: Database, event_type: str) -> dict[str, Any]:
    rows = await take(database, event_type)
    assert len(rows) == 1, [row.payload for row in rows]
    return rows[0].payload


async def test_a_screenshot_goes_from_upload_to_verdict(pipeline: Pipeline) -> None:
    fields = desktop_fields(chat_id=str(uuid.uuid4()))
    pipeline.extractor.script = [ok_result(fields)]
    result = await pipeline.ingest(1)
    await take(pipeline.database, EVENT_UPLOAD)
    submitted = await one(pipeline.database, EVENT_SUBMITTED)

    await pipeline.extract(submitted)
    assert len(pipeline.extractor.calls) == 1
    assert pipeline.extractor.calls[0]["hints"].url.startswith("https://seller.wildberries.ru")
    row = await pipeline.submission(result.submission_id)
    assert row.stage == "extracted" and row.attempts == 0
    assert row.case_kind == "desktop" and row.chat_id_source == "image" and row.chat_id_norm
    assert row.key_time == fields.review_at and row.key_time_precision == "minute"
    assert row.text_key == case_keys(fields)["text_key"] and len(row.text_key or "") == 40
    assert row.text_len == len(fields.text_norm or "") and row.buyer_hmac == fields.buyer_hmac

    async with pipeline.database.session() as session:
        extraction = await session.scalar(select(ExtractionModel))
        bits = await session.scalar(select(cast(FileModel.dhash, String)))
        thumb = await session.scalar(select(StorageObjectModel).where(StorageObjectModel.kind == "thumb"))
    assert extraction is not None and extraction.status == "ok" and extraction.vlm_model == "fake-vlm"
    assert extraction.vlm_json == {"layout": "desktop_window"}  # имя покупателя вырезано
    assert "buyer_name" not in str(extraction.fields)
    assert bits is not None and len(bits) == 64
    assert thumb is not None and thumb.content_type == "image/jpeg" and thumb.state == "staged"
    thumb_upload = await one(pipeline.database, EVENT_UPLOAD)
    assert thumb_upload["kind"] == "thumb" and thumb_upload["key"].startswith("dbs-screenshots/thumb/")

    extracted_rows = await take(pipeline.database, EVENT_EXTRACTED)
    assert extracted_rows[0].message_key == str(DECIDE_PARTITION_KEY)
    await pipeline.decide(extracted_rows[0].payload)
    row = await pipeline.submission(result.submission_id)
    assert row.verdict == row.auto_verdict == "accepted" and row.stage == "decided" and row.decided_at
    inp, candidates = pipeline.decider.calls[0]
    assert inp.extraction.fields == fields and inp.dhash is not None and inp.uploads_last_hour == 1
    assert candidates == []
    ready = await one(pipeline.database, EVENT_VERDICT_READY)
    assert ready["verdict"] == "accepted" and ready["previous_verdict"] is None

    # повторная доставка обоих событий ничего не меняет
    await pipeline.extract(submitted)
    await pipeline.decide(extracted_rows[0].payload)
    assert len(pipeline.extractor.calls) == 1 and len(pipeline.decider.calls) == 1
    async with pipeline.database.session() as session:
        assert len(list(await session.scalars(select(VerdictEventModel)))) == 1
    assert await take(pipeline.database, EVENT_EXTRACTED) == []
    assert await take(pipeline.database, EVENT_VERDICT_READY) == []


async def test_the_same_file_is_recognised_once(pipeline: Pipeline) -> None:
    first = await pipeline.ingest(2)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    async with pipeline.database.session() as session:
        await session.execute(
            update(SubmissionModel).where(SubmissionModel.id == first.submission_id).values(verdict="rejected")
        )
        await session.commit()

    second = await pipeline.ingest(2, user=OTHER)
    assert second.verdict is None  # копия отклонённого идёт по конвейеру
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    assert len(pipeline.extractor.calls) == 1
    row = await pipeline.submission(second.submission_id)
    assert (
        row.stage == "extracted" and row.chat_id_norm == (await pipeline.submission(first.submission_id)).chat_id_norm
    )


async def test_recognition_retries_on_schedule_and_then_goes_to_review(pipeline: Pipeline) -> None:
    pipeline.extractor.script = [
        TimeoutError("vlm timeout"),
        ExtractionResult(status="failed", error="invalid json"),
    ]
    result = await pipeline.ingest(3)
    payload = await one(pipeline.database, EVENT_SUBMITTED)
    attempts = BASE.screenshot_control.extraction_max_attempts

    for attempt in range(attempts - 1):
        await pipeline.extract(payload)
        assert len(pipeline.extractor.calls) == attempt + 1
        rows = await take(pipeline.database, EVENT_SUBMITTED)
        assert len(rows) == 1
        payload = rows[0].payload
        delay = RETRY_DELAYS[attempt]
        assert payload["attempt"] == attempt + 1
        assert datetime.fromisoformat(payload["not_before"]) == pipeline.clock.now + delay
        assert rows[0].next_attempt_at == pipeline.clock.now + delay  # публикатор не возьмёт раньше срока
        row = await pipeline.submission(result.submission_id)
        assert row.attempts == attempt + 1 and row.stage == "processing" and row.last_error

        # пришло раньше срока: переложить без вызова модели
        await pipeline.extract(payload)
        assert len(pipeline.extractor.calls) == attempt + 1
        payload = await one(pipeline.database, EVENT_SUBMITTED)
        pipeline.clock.advance(delay)

    await pipeline.extract(payload)
    assert len(pipeline.extractor.calls) == attempts
    assert await take(pipeline.database, EVENT_SUBMITTED) == []
    assert await take(pipeline.database, EVENT_EXTRACTED) == []
    row = await pipeline.submission(result.submission_id)
    assert row.verdict == "needs_review" and row.stage == "decided" and row.attempts == attempts
    assert [item["code"] for item in row.reasons] == ["extraction_failed"]
    async with pipeline.database.session() as session:
        extraction = await session.scalar(select(ExtractionModel))
    assert extraction is not None and extraction.status == "failed" and extraction.error == "invalid json"
    assert (await one(pipeline.database, EVENT_VERDICT_READY))["verdict"] == "needs_review"
    assert pipeline.decider.calls == []


async def test_a_later_screen_waits_for_an_earlier_one(pipeline: Pipeline) -> None:
    chat = str(uuid.uuid4())
    pipeline.extractor.script = [ok_result(desktop_fields(chat_id=chat))]
    pipeline.decider.verdict = "duplicate"
    early = await pipeline.ingest(4)
    late = await pipeline.ingest(5, user=OTHER)
    submitted = {row.payload["submission_id"]: row.payload for row in await take(pipeline.database, EVENT_SUBMITTED)}

    await pipeline.extract(submitted[str(late.submission_id)])
    late_extracted = await one(pipeline.database, EVENT_EXTRACTED)
    await pipeline.decide(late_extracted)
    assert (await pipeline.submission(late.submission_id)).stage == "extracted"
    deferred = await take(pipeline.database, EVENT_EXTRACTED)
    assert deferred[0].payload["deferrals"] == 1 and deferred[0].next_attempt_at > pipeline.clock.now
    assert pipeline.decider.calls == []

    await pipeline.extract(submitted[str(early.submission_id)])
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    assert (await pipeline.submission(early.submission_id)).verdict == "accepted"  # поздний не раньше его

    await pipeline.decide(deferred[0].payload)
    row = await pipeline.submission(late.submission_id)
    assert row.verdict == "duplicate" and row.duplicate_of == early.submission_id
    _, candidates = pipeline.decider.calls[-1]
    assert [item.submission_id for item in candidates] == [early.submission_id]
    assert candidates[0].fields is not None and candidates[0].fields.chat_id == chat


async def test_waiting_is_bounded(pipeline: Pipeline) -> None:
    await pipeline.ingest(6)
    late = await pipeline.ingest(7)
    submitted = {row.payload["submission_id"]: row.payload for row in await take(pipeline.database, EVENT_SUBMITTED)}
    await pipeline.extract(submitted[str(late.submission_id)])
    payload = await one(pipeline.database, EVENT_EXTRACTED)

    await pipeline.decide({**payload, "deferrals": MAX_DEFERRALS})
    assert (await pipeline.submission(late.submission_id)).verdict == "accepted"


async def test_a_screen_without_outcome_expires_as_rejected(pipeline: Pipeline) -> None:
    result = await pipeline.ingest(8, outcome=None)
    payload = await one(pipeline.database, EVENT_SUBMITTED)

    early = await expire_outcomes(pipeline.database, BASE.screenshot_control, datetime.now(UTC))
    assert early == 0
    later = datetime.now(UTC) + timedelta(hours=BASE.screenshot_control.outcome_ttl_hours, minutes=1)
    assert await expire_outcomes(pipeline.database, BASE.screenshot_control, later) == 1
    row = await pipeline.submission(result.submission_id)
    assert row.verdict == "rejected" and row.stage == "decided"
    assert [item["code"] for item in row.reasons] == ["no_outcome"]
    assert (await one(pipeline.database, EVENT_VERDICT_READY))["verdict"] == "rejected"
    assert await expire_outcomes(pipeline.database, BASE.screenshot_control, later) == 0

    await pipeline.extract(payload)  # распознавание опоздало: решение уже есть
    assert pipeline.extractor.calls == []


async def test_a_screen_without_outcome_waits_for_it_and_then_is_decided(pipeline: Pipeline) -> None:
    result = await pipeline.ingest(9, outcome=None)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    row = await pipeline.submission(result.submission_id)
    assert (row.stage, row.verdict, row.decided_at) == ("extracted", None, None)
    assert pipeline.decider.calls == [] and await take(pipeline.database, EVENT_VERDICT_READY) == []

    assert await pipeline.choose_outcome(result.submission_id, "amended") == "processing"
    extracted = await one(pipeline.database, EVENT_EXTRACTED)
    assert extracted["submission_id"] == str(result.submission_id) and extracted["status"] == "ok"
    await pipeline.decide(extracted)
    row = await pipeline.submission(result.submission_id)
    assert (row.stage, row.verdict, row.outcome, row.outcome_source) == ("decided", "accepted", "amended", "form")
    assert pipeline.decider.calls[0][0].outcome == "amended"
    assert (await one(pipeline.database, EVENT_VERDICT_READY))["verdict"] == "accepted"

    with pytest.raises(ServiceError) as locked:
        await pipeline.choose_outcome(result.submission_id, "deleted")
    assert locked.value.code == "outcome_locked"


async def test_an_outcome_chosen_during_recognition_is_used_without_a_second_event(pipeline: Pipeline) -> None:
    result = await pipeline.ingest(10, outcome=None)
    submitted = await one(pipeline.database, EVENT_SUBMITTED)
    assert await pipeline.choose_outcome(result.submission_id, "deleted") == "processing"
    assert await take(pipeline.database, EVENT_EXTRACTED) == []  # распознавание ещё идёт

    await pipeline.extract(submitted)
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    row = await pipeline.submission(result.submission_id)
    assert (row.verdict, row.outcome) == ("accepted", "deleted")


async def test_a_waiting_screen_expires_and_a_late_event_changes_nothing(pipeline: Pipeline) -> None:
    result = await pipeline.ingest(11, outcome=None)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    extracted = await one(pipeline.database, EVENT_EXTRACTED)
    await pipeline.decide(extracted)

    later = datetime.now(UTC) + timedelta(hours=BASE.screenshot_control.outcome_ttl_hours, minutes=1)
    assert await expire_outcomes(pipeline.database, BASE.screenshot_control, later) == 1
    row = await pipeline.submission(result.submission_id)
    assert (row.stage, row.verdict, [item["code"] for item in row.reasons]) == ("decided", "rejected", ["no_outcome"])
    with pytest.raises(ServiceError):
        await pipeline.choose_outcome(result.submission_id)

    await pipeline.decide({**extracted, "event_id": str(uuid.uuid4())})
    assert pipeline.decider.calls == [] and (await pipeline.submission(result.submission_id)).verdict == "rejected"


async def test_an_overdue_screen_without_outcome_is_decided_by_the_rules(pipeline: Pipeline) -> None:
    """После срока решает `decide` (строка 1 таблицы 8.2), а не ожидание."""
    result = await pipeline.ingest(12, outcome=None)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    pipeline.clock.advance(timedelta(hours=BASE.screenshot_control.outcome_ttl_hours, minutes=1))
    pipeline.decider.verdict = "rejected"
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    assert pipeline.decider.calls[0][0].outcome is None
    assert (await pipeline.submission(result.submission_id)).verdict == "rejected"


async def test_failed_recognition_without_outcome_waits_too(pipeline: Pipeline) -> None:
    pipeline.extractor.script = [ExtractionResult(status="failed", error="invalid json")]
    result = await pipeline.ingest(13, outcome=None)
    payload = await one(pipeline.database, EVENT_SUBMITTED)
    for attempt in range(BASE.screenshot_control.extraction_max_attempts):
        await pipeline.extract(payload)
        if rows := await take(pipeline.database, EVENT_SUBMITTED):
            payload = rows[0].payload
            pipeline.clock.advance(RETRY_DELAYS[attempt])
    row = await pipeline.submission(result.submission_id)
    assert (row.stage, row.verdict) == ("extracted", None)
    assert await take(pipeline.database, EVENT_VERDICT_READY) == []

    await pipeline.choose_outcome(result.submission_id, "positive")
    extracted = await one(pipeline.database, EVENT_EXTRACTED)
    assert extracted["status"] == "failed"
    pipeline.decider.verdict = "needs_review"
    await pipeline.decide(extracted)
    inp, _ = pipeline.decider.calls[0]
    assert inp.extraction.status == "failed" and inp.extraction.error == "invalid json"
    assert (await pipeline.submission(result.submission_id)).verdict == "needs_review"


async def test_a_screen_without_outcome_does_not_hold_others(pipeline: Pipeline) -> None:
    """Неподписанный скрин не задерживает решения других и не становится их оригиналом."""
    pipeline.decider.verdict = "duplicate"
    waiting = await pipeline.ingest(14, outcome=None)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))

    same_file = await pipeline.ingest(14, user=OTHER)
    assert same_file.verdict is None  # не дубль при приёме: оригинал без исхода не заявлен
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    assert (await pipeline.submission(same_file.submission_id)).verdict == "accepted"
    _, candidates = pipeline.decider.calls[-1]
    assert candidates == []  # ни отсрочки, ни кандидата из ждущего

    await pipeline.choose_outcome(waiting.submission_id)
    await pipeline.decide(await one(pipeline.database, EVENT_EXTRACTED))
    _, candidates = pipeline.decider.calls[-1]
    assert [item.submission_id for item in candidates] == [same_file.submission_id]  # теперь оригинал — он


async def test_expiry_leaves_decided_screens_alone(pipeline: Pipeline) -> None:
    """Точная копия без исхода решена при приёме: срок исхода её не переписывает."""
    await pipeline.ingest(15)
    copy = await pipeline.ingest(15, user=OTHER, outcome=None)
    assert copy.verdict == "duplicate"
    later = datetime.now(UTC) + timedelta(hours=BASE.screenshot_control.outcome_ttl_hours, minutes=1)
    assert await expire_outcomes(pipeline.database, BASE.screenshot_control, later) == 0
    assert (await pipeline.submission(copy.submission_id)).verdict == "duplicate"


@pytest.mark.parametrize("precision", ["minute", "day"])
def test_text_key_needs_minute_precision(precision: Literal["minute", "day"]) -> None:
    fields = replace(desktop_fields(chat_id="ABCDEF01-2345-6789-ABCD-EF0123456789"), review_time_precision=precision)
    keys = case_keys(fields)
    assert keys["chat_id_norm"] == "abcdef01-2345-6789-abcd-ef0123456789"
    assert (keys["text_key"] is not None) == (precision == "minute")
    assert case_keys(replace(fields, chat_id="too-short"))["chat_id_norm"] is None
