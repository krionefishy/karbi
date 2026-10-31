"""Цепочка приём → распознавание → решение на PostgreSQL с фейками распознавания, решения и S3."""

import json
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
from backend.modules.wb_screenshot_control.application.pipeline import (
    MAX_DEFERRALS,
    MAX_ORDER_WAIT,
    PARKED_NOTE,
    RETRY_DELAYS,
    case_keys,
    park_failed_event,
    queued_at,
)
from backend.modules.wb_screenshot_control.application.submissions_service import SubmissionsService
from backend.modules.wb_screenshot_control.domain.normalize import ScreenDateTime, resolve_screen_datetime
from backend.modules.wb_screenshot_control.domain.pipeline_types import ExtractionResult
from backend.modules.wb_screenshot_control.domain.similarity import text_minhash
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    FileModel,
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.user_settings import UserSettingsRepository
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
from backend.workers.wb_screenshot_control.consumers import MAX_ATTEMPTS, EventConsumer

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
    assert deferred[0].payload["deferrals"] == 1
    # Первая перепроверка через секунды: пачка не выстраивается в очередь по полминуты.
    assert pipeline.clock.now < deferred[0].next_attempt_at <= pipeline.clock.now + timedelta(seconds=5)
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
    assert timedelta(minutes=3) >= MAX_ORDER_WAIT
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


def test_case_keys_carry_the_text_minhash() -> None:
    fields = desktop_fields()
    keys = case_keys(fields)
    assert keys["text_minhash"] == list(text_minhash(fields.text_norm) or ())
    assert case_keys(replace(fields, text_norm="брак"))["text_minhash"] is None


class PoisonDecider(FakeDecider):
    """Падает на одном скрине при каждой попытке — как баг или нарушение ограничения БД."""

    def __init__(self, poisoned: uuid.UUID) -> None:
        super().__init__()
        self.poisoned = poisoned

    def decide(self, inp: Any, candidates: Any) -> Any:
        if inp.submission_id == self.poisoned:
            raise RuntimeError("constraint violated")
        return super().decide(inp, candidates)


async def test_a_poison_message_is_parked_and_other_decisions_go_on(pipeline: Pipeline) -> None:
    pipeline.extractor.script = [ok_result(desktop_fields(chat_id=str(uuid.uuid4())))] * 2
    poisoned = await pipeline.ingest(31)
    healthy = await pipeline.ingest(32, user=OTHER)
    for row in await take(pipeline.database, EVENT_SUBMITTED):
        await pipeline.extract(row.payload)
    pipeline.decider = PoisonDecider(poisoned.submission_id)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    consumer = EventConsumer(
        topic="extracted",
        bootstrap_servers="",
        group_id="test",
        handler=pipeline.decide,
        on_poison=pipeline.services.park,
        sleep=sleep,
    )
    rows = await take(pipeline.database, EVENT_EXTRACTED)
    assert len(rows) == 2
    first, second = (json.dumps(row.payload).encode() for row in rows)

    outcomes = [await consumer.handle(first, position="0:1") for _ in range(MAX_ATTEMPTS)]
    assert outcomes == [False] * (MAX_ATTEMPTS - 1) + [True], "после пятой неудачи оффсет коммитится"
    assert len(slept) == MAX_ATTEMPTS - 1 and consumer.failures == {}
    assert await consumer.handle(second, position="0:2") is True

    parked = await pipeline.submission(poisoned.submission_id)
    assert (parked.verdict, parked.stage) == ("needs_review", "decided")
    assert parked.reasons[0]["code"] == "extraction_failed"
    assert "constraint" in parked.reasons[0]["detail"]["pipeline_error"]
    async with pipeline.database.session() as session:
        event = await session.scalar(
            select(VerdictEventModel).where(VerdictEventModel.submission_id == poisoned.submission_id)
        )
    assert event is not None and event.actor == "system" and event.note == PARKED_NOTE
    assert (await pipeline.submission(healthy.submission_id)).verdict == "accepted"

    # уже решённый скрин ядовитое сообщение не трогает
    now = pipeline.clock.now
    assert not await park_failed_event(pipeline.database, {"submission_id": str(healthy.submission_id)}, "x", now=now)
    assert not await park_failed_event(pipeline.database, {"submission_id": "not-a-uuid"}, "x", now=now)


def test_a_capture_from_the_future_is_a_clock_problem_not_a_queue() -> None:
    """Раньше `queued_at` всегда был временем снимка, и проверка `clock_skew` не срабатывала никогда."""
    received = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
    assert queued_at(received + timedelta(minutes=40), received) is None  # часы впереди → clock_skew
    assert queued_at(received - timedelta(days=4), received) == received - timedelta(days=4)  # → late_upload
    assert queued_at(None, received) is None


# --- пояс менеджера ---


@dataclass
class ScreenExtractor:
    """Читает время «как на экране» по очереди из `screens` и, как настоящий, разрешает его в поясе загрузившего."""

    screens: list[tuple[int, int, int, int]]
    chat_id: str = "5a1c2e3f-4b6d-4e8f-9a0b-1c2d3e4f5a6b"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def extract(
        self, image: bytes, *, channel: str, hints: Any, received_at: datetime, timezone: str | None = None
    ) -> ExtractionResult:
        day, month, hour, minute = self.screens[len(self.calls)]
        self.calls.append({"timezone": timezone, "received_at": received_at})
        zone = timezone or "Europe/Moscow"
        screen_time = ScreenDateTime(day=day, month=month, hour=hour, minute=minute)
        review_at, precision = resolve_screen_datetime(screen_time, received_at=received_at, timezone=zone)
        fields = replace(
            desktop_fields(self.chat_id),
            review_at=review_at,
            review_time_precision=precision,
            review_screen=screen_time,
            timezone=zone,
        )
        return ok_result(fields)


async def set_zone(database: Database, user: uuid.UUID, zone: str) -> None:
    async with database.session() as session:
        await UserSettingsRepository(session).set_timezone(user, zone, datetime.now(UTC))
        await session.commit()


async def decide_for_real(pipeline: Pipeline, payload: dict[str, Any]) -> None:
    service = pipeline.services.decision()
    service.now = pipeline.clock
    await service.handle(payload)


async def test_the_same_file_from_another_zone_is_not_reread_but_gets_its_own_moment(pipeline: Pipeline) -> None:
    pipeline.extractor = ScreenExtractor([(22, 10, 6, 39)])  # type: ignore[assignment]
    await set_zone(pipeline.database, OTHER, "Asia/Yekaterinburg")
    first = await pipeline.ingest(7)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    async with pipeline.database.session() as session:
        # Копия отклонённого идёт по конвейеру (копия живого решается сразу при приёме, без распознавания).
        await session.execute(
            update(SubmissionModel)
            .where(SubmissionModel.id == first.submission_id)
            .values(verdict="rejected", stage="decided")
        )
        await session.commit()
    second = await pipeline.ingest(7, user=OTHER)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))

    assert [call["timezone"] for call in pipeline.extractor.calls] == ["Europe/Moscow"]  # файл прочитан один раз
    msk, ekb = await pipeline.submission(first.submission_id), await pipeline.submission(second.submission_id)
    assert (msk.review_tz, ekb.review_tz) == ("Europe/Moscow", "Asia/Yekaterinburg")
    assert msk.key_time is not None and ekb.key_time is not None
    year = msk.key_time.year
    assert msk.key_time == datetime(year, 10, 22, 3, 39, tzinfo=UTC)
    assert ekb.key_time == datetime(year, 10, 22, 1, 39, tzinfo=UTC)
    assert msk.text_key != ekb.text_key and msk.chat_id_norm == ekb.chat_id_norm

    # Решение видит поля сабмишена в поясе его менеджера.
    service = pipeline.services.decision(pipeline.decider)
    service.now = pipeline.clock
    extracted = [row.payload for row in await take(pipeline.database, EVENT_EXTRACTED)]
    await service.handle(next(item for item in extracted if item["submission_id"] == str(second.submission_id)))
    inp, _ = pipeline.decider.calls[0]
    assert inp.extraction.fields is not None
    assert inp.extraction.fields.review_at == ekb.key_time and inp.extraction.fields.timezone == "Asia/Yekaterinburg"

    # Смена пояса не пересчитывает уже прочитанное: карточка и пояс скрина прежние.
    await set_zone(pipeline.database, OTHER, "Asia/Vladivostok")
    async with pipeline.database.session() as session:
        view = await SubmissionsService(session, pipeline.services.config, ImageLinks(None, 300)).card(
            OTHER, second.submission_id
        )
    assert view["fields"]["review_at"] == datetime(year, 10, 22, 1, 39, tzinfo=UTC)
    assert (await pipeline.submission(second.submission_id)).review_tz == "Asia/Yekaterinburg"


async def test_the_same_file_from_managers_in_different_zones_is_a_duplicate(pipeline: Pipeline) -> None:
    pipeline.extractor = ScreenExtractor([(22, 10, 6, 39)])  # type: ignore[assignment]
    await set_zone(pipeline.database, OTHER, "Asia/Yekaterinburg")
    first = await pipeline.ingest(12)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    await decide_for_real(pipeline, await one(pipeline.database, EVENT_EXTRACTED))
    second = await pipeline.ingest(12, user=OTHER)
    copy = await pipeline.submission(second.submission_id)
    assert second.verdict == "duplicate" and copy.duplicate_of == first.submission_id
    assert len(pipeline.extractor.calls) == 1


async def test_two_screens_of_one_review_from_managers_in_different_zones_meet_in_utc(pipeline: Pipeline) -> None:
    # Москва видит 06:39, Екатеринбург — 08:39: у честных менеджеров с верным поясом это один момент.
    pipeline.extractor = ScreenExtractor([(22, 10, 6, 39), (22, 10, 8, 39)])  # type: ignore[assignment]
    await set_zone(pipeline.database, OTHER, "Asia/Yekaterinburg")
    first = await pipeline.ingest(8)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    second = await pipeline.ingest(9, user=OTHER)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    assert [call["timezone"] for call in pipeline.extractor.calls] == ["Europe/Moscow", "Asia/Yekaterinburg"]
    msk, ekb = await pipeline.submission(first.submission_id), await pipeline.submission(second.submission_id)
    assert msk.key_time == ekb.key_time and msk.text_key == ekb.text_key

    for row in await take(pipeline.database, EVENT_EXTRACTED):
        await decide_for_real(pipeline, row.payload)
    ekb = await pipeline.submission(second.submission_id)
    assert ekb.verdict == "duplicate" and ekb.duplicate_of == first.submission_id
    assert {"case_key", "cross_user_duplicate"} <= {reason["code"] for reason in ekb.reasons}


async def test_a_manager_with_a_wrong_zone_goes_to_review(pipeline: Pipeline) -> None:
    # Менеджер в Омске (09:39 на экране) оставил Москву: момент на 3 часа позже — «тот же чат, другое время».
    pipeline.extractor = ScreenExtractor([(22, 10, 6, 39), (22, 10, 9, 39)])  # type: ignore[assignment]
    await pipeline.ingest(10)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    second = await pipeline.ingest(11, user=OTHER)
    await pipeline.extract(await one(pipeline.database, EVENT_SUBMITTED))
    for row in await take(pipeline.database, EVENT_EXTRACTED):
        await decide_for_real(pipeline, row.payload)
    late = await pipeline.submission(second.submission_id)
    assert late.verdict == "needs_review"
    assert "same_chat_same_text_other_time" in {reason["code"] for reason in late.reasons}
