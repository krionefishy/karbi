"""Конвейер на outbox: `submitted` → распознавание → `extracted` → решение → `verdict-ready`.

Модель и решение вызываются вне транзакции; запись результата, следующее событие и отметка
в `inbox_events` — одной транзакцией, поэтому повторная доставка ничего не портит.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.events import (
    EVENT_EXTRACTED,
    EVENT_SUBMITTED,
    ScreenshotExtracted,
    ScreenshotObjectUpload,
    ScreenshotSubmitted,
)
from backend.modules.wb_screenshot_control.application.inbox import parse_event
from backend.modules.wb_screenshot_control.application.ingest import staging_path
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.serialization import (
    fields_from_json,
    fields_to_json,
    match_to_json,
    reason_to_json,
    without_personal,
)
from backend.modules.wb_screenshot_control.application.storage import ObjectReader
from backend.modules.wb_screenshot_control.application.thumbnails import Rendered, render
from backend.modules.wb_screenshot_control.application.verdicts import decide_system, reason
from backend.modules.wb_screenshot_control.domain import STAGE_EXTRACTED, STAGE_PROCESSING, object_key
from backend.modules.wb_screenshot_control.domain.hashing import from_bits, to_bits
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ClientHints,
    DecisionInput,
    ExtractedFields,
    ExtractionResult,
)
from backend.modules.wb_screenshot_control.domain.ports import Decider, Extractor, Staging
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import ExtractionModel, SubmissionModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import (
    CandidateQuery,
    CandidateRow,
    ScreenshotRepository,
)
from backend.shared.settings.config import ScreenshotControlConfig
from backend.storage.pg import Database

# Паузы перед повтором распознавания: после 1-й, 2-й, 3-й, 4-й неудачи.
RETRY_DELAYS = (timedelta(seconds=15), timedelta(seconds=60), timedelta(minutes=5), timedelta(minutes=20))
# Решение ждёт более ранний скрин моложе получаса, перепроверяя раз в 30 секунд.
ORDER_WINDOW = timedelta(minutes=30)
DEFER_DELAY = timedelta(seconds=30)
MAX_DEFERRALS = 60
MIN_MESSAGE_LEN = 8

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:500]


def awaits_outcome(submission: SubmissionModel, now: datetime, config: ScreenshotControlConfig) -> bool:
    """Исход не выбран, а срок `outcome_ttl_hours` не вышел: вердикта нет, менеджер видит `awaiting_outcome`.

    Скрин без подписи — ещё не заявка (единица работы — скрин + исход), поэтому до исхода он не решается
    и не занимает место оригинала для чужих скринов (`candidates`, `exact_original`).
    """
    return submission.outcome is None and submission.received_at > now - timedelta(hours=config.outcome_ttl_hours)


def case_keys(fields: ExtractedFields) -> dict[str, Any]:
    """Ключи кейса для индексов `submissions`; `text_key` = sha1(минута|text_norm) только при точности до минуты."""
    key_time = fields.review_at
    precision = fields.review_time_precision if key_time is not None else None
    text_norm = fields.text_norm[:4000] if fields.text_norm else None
    text_key = None
    if key_time is not None and precision == "minute" and text_norm:
        minute = key_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
        text_key = hashlib.sha1(f"{minute}|{text_norm}".encode()).hexdigest()
    chat_id = fields.chat_id.lower() if fields.chat_id and len(fields.chat_id) == 36 else None
    return {
        "case_kind": fields.case_kind,
        "chat_id_norm": chat_id,
        "chat_id_source": fields.chat_id_source if chat_id else None,
        "key_time": key_time,
        "key_time_precision": precision,
        "text_norm": text_norm,
        "text_len": min(len(text_norm), 32_767) if text_norm else None,
        "text_key": text_key,
        "buyer_hmac": fields.buyer_hmac[:16] if fields.buyer_hmac else None,
        "article_norm": fields.article_norm[:32] if fields.article_norm else None,
    }


@dataclass(frozen=True, slots=True)
class _Work:
    submission_id: uuid.UUID
    file_id: uuid.UUID
    sha256: str
    channel: str
    received_at: datetime
    hints: ClientHints
    reuse: bool
    render: bool
    thumb: bool


class ExtractionService:
    """Консьюмер `ScreenshotSubmitted`: хеш, миниатюра, распознавание и ключи кейса."""

    def __init__(
        self,
        database: Database,
        staging: Staging,
        reader: ObjectReader,
        extractor: Extractor,
        config: ScreenshotControlConfig,
        *,
        bucket: str,
        now: Clock = _utcnow,
    ) -> None:
        self.database = database
        self.staging = staging
        self.reader = reader
        self.extractor = extractor
        self.config = config
        self.bucket = bucket
        self.now = now
        self.logger = logging.getLogger("wb.screenshot_control.extract")

    async def handle(self, payload: dict[str, Any]) -> None:
        event, event_id = parse_event(ScreenshotSubmitted, payload)
        work = await self._prepare(event, event_id)
        if work is None:
            return
        rendered: Rendered | None = None
        result: ExtractionResult | None = None
        error: str | None = None
        try:
            if work.render or not work.reuse:
                async with self.database.session() as session:
                    stored = await ScreenshotRepository(session).storage_object(work.file_id, "orig")
                if stored is None:
                    raise FileNotFoundError("original is not registered")
                data = await self.reader.read(stored)
                if work.render:
                    rendered = await asyncio.to_thread(render, data)
                    if work.thumb:
                        await self.staging.write(staging_path(object_key(work.sha256, "thumb")), rendered.thumbnail)
                if not work.reuse:
                    result = await self.extractor.extract(
                        data, channel=work.channel, hints=work.hints, received_at=work.received_at
                    )
                    if result.status != "ok" or result.fields is None:
                        error = (result.error or "extraction returned no fields")[:500]
        except Exception as failure:
            error = _error(failure)
        await self._finish(event, event_id, work, rendered, result, error)

    async def _prepare(self, event: ScreenshotSubmitted, event_id: uuid.UUID) -> _Work | None:
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return None
            if event.not_before is not None and event.not_before > self.now():
                publish(session, event.model_copy(update={"event_id": None}), not_before=event.not_before)
                repository.mark_inbox(event_id, EVENT_SUBMITTED)
                await session.commit()
                return None
            submission = await repository.submission(event.submission_id)
            if submission is None or submission.stage != STAGE_PROCESSING:
                repository.mark_inbox(event_id, EVENT_SUBMITTED)
                await session.commit()
                return None
            file = await repository.file(submission.file_id)
            assert file is not None
            extraction = await repository.extraction(file.id, self.config.pipeline_version)
            thumb = await repository.storage_object(file.id, "thumb")
            return _Work(
                submission_id=submission.id,
                file_id=file.id,
                sha256=file.sha256,
                channel=submission.channel,
                received_at=submission.received_at,
                hints=ClientHints(
                    url=submission.client_url,
                    captured_at=submission.client_captured_at,
                    tz_offset_min=submission.client_tz_offset_min,
                ),
                reuse=extraction is not None and extraction.status == "ok",
                render=file.dhash is None or thumb is None,
                thumb=thumb is None,
            )

    async def _finish(
        self,
        event: ScreenshotSubmitted,
        event_id: uuid.UUID,
        work: _Work,
        rendered: Rendered | None,
        result: ExtractionResult | None,
        error: str | None,
    ) -> None:
        now = self.now()
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return
            submission = await repository.submission(work.submission_id, for_update=True)
            if submission is not None and submission.stage == STAGE_PROCESSING:
                if rendered is not None:
                    await self._store_render(session, repository, work, rendered)
                if error is None:
                    await self._extracted(session, repository, submission, result)
                else:
                    await self._failed(session, repository, submission, event, error, now)
            repository.mark_inbox(event_id, EVENT_SUBMITTED)
            await session.commit()
        if error is not None:
            self.logger.warning(
                "screenshot_extract_failed",
                extra={"submission_id": str(work.submission_id), "attempt": event.attempt, "error": error},
            )

    async def _store_render(
        self, session: AsyncSession, repository: ScreenshotRepository, work: _Work, rendered: Rendered
    ) -> None:
        await repository.set_dhash(work.file_id, to_bits(rendered.dhash))
        if not work.thumb:
            return
        key = object_key(work.sha256, "thumb")
        path = staging_path(key)
        sha256 = hashlib.sha256(rendered.thumbnail).hexdigest()
        values: dict[str, Any] = {
            "file_id": work.file_id,
            "kind": "thumb",
            "bucket": self.bucket,
            "s3_key": key,
            "staging_path": path,
            "content_type": "image/jpeg",
            "size_bytes": len(rendered.thumbnail),
            "sha256": sha256,
            "state": "staged",
        }
        if await repository.insert_storage_object(values) and self.bucket:
            publish(
                session,
                ScreenshotObjectUpload(
                    file_id=work.file_id,
                    kind="thumb",
                    bucket=self.bucket,
                    key=key,
                    staging_path=path,
                    sha256=sha256,
                    content_type="image/jpeg",
                    size_bytes=len(rendered.thumbnail),
                ),
            )

    async def _extracted(
        self,
        session: AsyncSession,
        repository: ScreenshotRepository,
        submission: SubmissionModel,
        result: ExtractionResult | None,
    ) -> None:
        version = self.config.pipeline_version
        if result is not None and result.fields is not None:
            extraction_id = await repository.upsert_extraction(
                {
                    "file_id": submission.file_id,
                    "pipeline_version": version,
                    "is_current": True,
                    "status": "ok",
                    "layout": result.fields.layout[:24],
                    "vlm_model": result.vlm_model,
                    "vlm_prompt": result.vlm_prompt[:16] if result.vlm_prompt else None,
                    "vlm_json": without_personal(result.vlm_json),
                    "ocr_json": without_personal(result.ocr_json),
                    "fields": fields_to_json(result.fields),
                    "agreement": result.fields.agreement or None,
                    "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out,
                    "latency_ms": result.latency_ms,
                    "error": None,
                },
                keep_ok=False,
            )
            fields = result.fields
        else:
            current = await repository.extraction(submission.file_id, version)
            assert current is not None and current.fields is not None
            extraction_id, fields = current.id, fields_from_json(current.fields)
        for name, value in case_keys(fields).items():
            setattr(submission, name, value)
        submission.stage = STAGE_EXTRACTED
        submission.last_error = None
        submission.updated_at = self.now()
        publish(
            session,
            ScreenshotExtracted(
                submission_id=submission.id,
                file_id=submission.file_id,
                extraction_id=extraction_id,
                pipeline_version=version,
                status="ok",
            ),
        )

    async def _failed(
        self,
        session: AsyncSession,
        repository: ScreenshotRepository,
        submission: SubmissionModel,
        event: ScreenshotSubmitted,
        error: str,
        now: datetime,
    ) -> None:
        attempt = event.attempt + 1
        submission.attempts = min(attempt, 32_767)
        submission.last_error = error
        submission.updated_at = now
        if attempt < self.config.extraction_max_attempts:
            not_before = now + RETRY_DELAYS[min(event.attempt, len(RETRY_DELAYS) - 1)]
            retry = event.model_copy(update={"event_id": None, "attempt": attempt, "not_before": not_before})
            publish(session, retry, not_before=not_before)
            return
        await repository.upsert_extraction(
            {
                "file_id": submission.file_id,
                "pipeline_version": self.config.pipeline_version,
                "is_current": True,
                "status": "failed",
                "error": error,
            },
            keep_ok=True,
        )
        if awaits_outcome(submission, now, self.config):
            # Решение (needs_review/extraction_failed) — когда выберут исход; без исхода к сроку — отказ.
            submission.stage = STAGE_EXTRACTED
            return
        # Сбой нашей стороны менеджера не штрафует: на ручную проверку, а не в отказ.
        decide_system(
            session,
            submission,
            "needs_review",
            [reason("extraction_failed", "warn", attempts=attempt)],
            now=now,
        )


class DecisionService:
    """Консьюмер `ScreenshotExtracted`: кандидаты из базы, решение `Decider`, вердикт.

    Все `extracted` идут одним ключом партиции, поэтому решения принимаются по одному.
    """

    def __init__(
        self, database: Database, decider: Decider, config: ScreenshotControlConfig, *, now: Clock = _utcnow
    ) -> None:
        self.database = database
        self.decider = decider
        self.config = config
        self.now = now
        self.logger = logging.getLogger("wb.screenshot_control.decide")

    async def handle(self, payload: dict[str, Any]) -> None:
        event, event_id = parse_event(ScreenshotExtracted, payload)
        now = self.now()
        async with self.database.session() as session:
            repository = ScreenshotRepository(session)
            if await repository.inbox_processed(event_id):
                return
            submission = await repository.submission(event.submission_id, for_update=True)
            if submission is None or submission.stage != STAGE_EXTRACTED or self._awaits_outcome(submission, now):
                # Без исхода не решаем до срока: исход запустит решение заново, срок — `expire_outcomes`.
                repository.mark_inbox(event_id, EVENT_EXTRACTED)
                await session.commit()
                return
            if event.deferrals < MAX_DEFERRALS and await repository.earlier_undecided(submission, now - ORDER_WINDOW):
                # Более ранний скрин ещё в работе: решать после него, иначе поздний займёт место оригинала.
                later = event.model_copy(update={"event_id": None, "deferrals": event.deferrals + 1})
                publish(session, later, not_before=now + DEFER_DELAY)
                repository.mark_inbox(event_id, EVENT_EXTRACTED)
                await session.commit()
                return
            extraction = await repository.extraction(submission.file_id, self.config.pipeline_version)
            inp = await self._input(repository, submission, extraction)
            rows = await repository.candidates(self._query(submission, inp, now))
            decision = self.decider.decide(inp, [self._candidate(row) for row in rows])
            decide_system(
                session,
                submission,
                decision.verdict,
                [reason_to_json(item) for item in decision.reasons],
                now=now,
                matches=[match_to_json(item) for item in decision.matches],
                duplicate_of=decision.duplicate_of,
                audit_requested=decision.audit_requested,
            )
            repository.mark_inbox(event_id, EVENT_EXTRACTED)
            await session.commit()
        self.logger.info(
            "screenshot_decided",
            extra={"submission_id": str(event.submission_id), "verdict": decision.verdict, "candidates": len(rows)},
        )

    def _awaits_outcome(self, submission: SubmissionModel, now: datetime) -> bool:
        return awaits_outcome(submission, now, self.config)

    async def _input(
        self, repository: ScreenshotRepository, submission: SubmissionModel, extraction: ExtractionModel | None
    ) -> DecisionInput:
        bits = await repository.file_dhash(submission.file_id)
        file = await repository.file(submission.file_id)
        assert file is not None
        if extraction is not None and extraction.status == "ok" and extraction.fields is not None:
            result = ExtractionResult(
                status="ok",
                fields=fields_from_json(extraction.fields),
                vlm_model=extraction.vlm_model,
                vlm_prompt=extraction.vlm_prompt,
                vlm_json=extraction.vlm_json,
                ocr_json=extraction.ocr_json,
                tokens_in=extraction.tokens_in,
                tokens_out=extraction.tokens_out,
                latency_ms=extraction.latency_ms,
            )
        else:
            result = ExtractionResult(status="failed", error=extraction.error if extraction else "no extraction")
        return DecisionInput(
            submission_id=submission.id,
            user_id=submission.user_id,
            channel=submission.channel,  # type: ignore[arg-type]
            received_at=submission.received_at,
            outcome=submission.outcome,
            file_sha256=file.sha256,
            dhash=from_bits(bits) if bits else None,
            extraction=result,
            hints=ClientHints(
                url=submission.client_url,
                captured_at=submission.client_captured_at,
                tz_offset_min=submission.client_tz_offset_min,
            ),
            uploads_last_hour=await repository.uploads_in_hour(submission.user_id, submission.received_at),
            queued_at=submission.client_captured_at,
        )

    def _query(self, submission: SubmissionModel, inp: DecisionInput, now: datetime) -> CandidateQuery:
        fields = inp.extraction.fields
        texts: tuple[str, ...] = ()
        if fields is not None and fields.case_kind == "mobile":
            texts = tuple(
                sorted({item.text_norm for item in fields.messages if len(item.text_norm) >= MIN_MESSAGE_LEN})
            )
        return CandidateQuery(
            submission_id=submission.id,
            file_id=submission.file_id,
            since=now - timedelta(days=self.config.dedupe_window_days),
            pipeline_version=self.config.pipeline_version,
            chat_id=submission.chat_id_norm,
            text_key=submission.text_key,
            key_time=submission.key_time,
            day_precision=submission.key_time_precision == "day",
            dhash_bits=to_bits(inp.dhash) if inp.dhash is not None else None,
            dhash_max_distance=self.config.thresholds.near_dhash_soft,
            message_texts=texts,
        )

    @staticmethod
    def _candidate(row: CandidateRow) -> Candidate:
        return Candidate(
            submission_id=row.submission.id,
            user_id=row.submission.user_id,
            received_at=row.submission.received_at,
            verdict=row.submission.verdict,
            outcome=row.submission.outcome,
            file_sha256=row.sha256,
            dhash=from_bits(row.dhash_bits) if row.dhash_bits else None,
            fields=fields_from_json(row.extraction_fields) if row.extraction_fields else None,
        )
