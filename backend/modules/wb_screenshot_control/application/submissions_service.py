"""Скриншоты менеджера: приём, список, карточка, исход и ссылка на миниатюру. Только свои.

Менеджер видит статус и узкий словарь причин (`domain/manager_reasons`); чей оригинал у дубля,
совпавшие поля и пороги не раскрываются.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.errors import (
    ServiceError,
    decode_cursor,
    encode_cursor,
    not_found,
)
from backend.modules.wb_screenshot_control.application.events import ScreenshotExtracted, ScreenshotSubmitted
from backend.modules.wb_screenshot_control.application.images import ImageLinks
from backend.modules.wb_screenshot_control.application.ingest import IngestError, IngestService
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.domain import STAGE_DECIDED, STAGE_EXTRACTED, STAGE_PROCESSING
from backend.modules.wb_screenshot_control.domain.manager_reasons import manager_reason
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    StorageObjectModel,
    SubmissionModel,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import ScreenshotQueries, Window
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig

Clock = Callable[[], datetime]
TEXT_PREVIEW = 200
CHAT_TAIL = 6


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reason_codes(submission: SubmissionModel) -> list[str]:
    return [str(item.get("code")) for item in submission.reasons or [] if isinstance(item, dict)]


def manager_status(submission: SubmissionModel) -> str:
    if submission.verdict is not None:
        return submission.verdict
    return "processing" if submission.outcome else "awaiting_outcome"


def manager_fields(extraction: ExtractionModel | None) -> dict[str, Any] | None:
    """Что прочитано — для проверки глазами: от chatId только хвост, имени покупателя нет вовсе."""
    if extraction is None or extraction.status != "ok" or not extraction.fields:
        return None
    fields = extraction.fields
    review_at = fields.get("review_at")
    stars = fields.get("stars")
    text = fields.get("review_text") or fields.get("text_norm")
    chat_id = fields.get("chat_id")
    return {
        "review_at": datetime.fromisoformat(review_at) if review_at else None,
        "stars": stars if isinstance(stars, int) and 1 <= stars <= 5 else None,
        "text_preview": str(text)[:TEXT_PREVIEW] if text else None,
        "chat_tail": str(chat_id)[-CHAT_TAIL:] if chat_id else None,
    }


class SubmissionsService:
    def __init__(
        self,
        session: AsyncSession,
        config: ScreenshotControlConfig,
        images: ImageLinks,
        *,
        now: Clock = _utcnow,
    ) -> None:
        self.session = session
        self.config = config
        self.images = images
        self.queries = ScreenshotQueries(session)
        self.now = now

    async def submit(
        self, ingest: IngestService, user_id: uuid.UUID, channel: str, data: bytes, **options: Any
    ) -> tuple[dict[str, Any], bool]:
        """Карточка принятого скрина и признак «новый»: повтор `client_submission_id` отвечает той же."""
        try:
            result = await ingest.ingest(user_id, channel, data, **options)
        except IngestError as error:
            raise ServiceError(error.code, error.message) from error
        submission = await self.queries.own_submission(user_id, result.submission_id)
        if submission is None:
            raise not_found()
        return (await self.views([submission]))[0], result.created

    async def page(
        self,
        user_id: uuid.UUID,
        *,
        window: Window,
        status: str | None = None,
        outcome: str | None = None,
        channel: str | None = None,
        cursor: str | None = None,
        limit: int,
    ) -> dict[str, Any]:
        after = None
        if cursor:
            stamp, last_id = decode_cursor(cursor, 2)
            try:
                after = (datetime.fromisoformat(stamp), uuid.UUID(last_id))
            except ValueError as error:
                raise ServiceError("bad_cursor", "Курсор страницы не подходит, начните список заново") from error
        rows = await self.queries.own_submissions(
            user_id, window=window, status=status, outcome=outcome, channel=channel, after=after, limit=limit + 1
        )
        more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].received_at.isoformat(), rows[-1].id) if more else None
        return {"items": await self.views(rows), "next_cursor": next_cursor}

    async def card(self, user_id: uuid.UUID, submission_id: uuid.UUID) -> dict[str, Any]:
        submission = await self.queries.own_submission(user_id, submission_id)
        if submission is None:
            raise not_found()
        return (await self.views([submission]))[0]

    async def change_outcome(self, user_id: uuid.UUID, submission_id: uuid.UUID, outcome: str) -> dict[str, Any]:
        """Исход меняется, пока вердикта нет.

        Исключение — отказ «не выбран исход» в пределах `outcome_ttl_hours`: конвейер решает сразу
        и без исхода ставит `rejected/no_outcome`, не дожидаясь срока. Тогда исход принимается, а
        решение пересчитывается тем же консьюмером (stage=extracted и новое `screenshot.extracted`).
        """
        now = self.now()
        submission = await self.queries.own_submission(user_id, submission_id, for_update=True)
        if submission is None:
            raise not_found()
        rerun = self._waits_for_outcome(submission, now)
        if submission.verdict is not None and not rerun:
            raise ServiceError("outcome_locked", "Исход уже не поменять: решение по скриншоту принято")
        submission.outcome = outcome
        submission.outcome_source = "form"
        submission.outcome_set_at = now
        submission.updated_at = now
        if rerun:
            await self._decide_again(submission, f"user:{user_id}")
        await self.session.commit()
        return (await self.views([submission]))[0]

    def _waits_for_outcome(self, submission: SubmissionModel, now: datetime) -> bool:
        return (
            submission.verdict == "rejected"
            and submission.reviewed_by is None
            and submission.stage == STAGE_DECIDED
            and "no_outcome" in reason_codes(submission)
            and submission.received_at > now - timedelta(hours=self.config.outcome_ttl_hours)
        )

    async def _decide_again(self, submission: SubmissionModel, actor: str) -> None:
        repository = ScreenshotRepository(self.session)
        repository.add_verdict_event(
            submission.id,
            actor=actor,
            from_verdict=submission.verdict,
            to_verdict=None,
            reasons=[],
            note="исход выбран, решение пересчитывается",
        )
        reset_verdict(submission)
        extraction = await repository.extraction(submission.file_id, self.config.pipeline_version)
        if extraction is not None:
            submission.stage = STAGE_EXTRACTED
            publish(
                self.session,
                ScreenshotExtracted(
                    submission_id=submission.id,
                    file_id=submission.file_id,
                    extraction_id=extraction.id,
                    pipeline_version=self.config.pipeline_version,
                    status=extraction.status,  # type: ignore[arg-type]
                ),
            )
            return
        file = await repository.file(submission.file_id)
        assert file is not None
        submission.stage = STAGE_PROCESSING
        publish(
            self.session,
            ScreenshotSubmitted(
                submission_id=submission.id,
                file_id=submission.file_id,
                sha256=file.sha256,
                channel=submission.channel,  # type: ignore[arg-type]
                received_at=submission.received_at,
            ),
        )

    async def image_url(self, user_id: uuid.UUID, submission_id: uuid.UUID) -> dict[str, Any]:
        """Менеджеру — только миниатюра своего скрина; оригинал видит админ."""
        submission = await self.queries.own_submission(user_id, submission_id)
        if submission is None:
            raise not_found()
        objects = await self.queries.objects([submission.file_id])
        return self.images.answer(objects.get((submission.file_id, "thumb")), now=self.now())

    async def views(self, submissions: Sequence[SubmissionModel]) -> list[dict[str, Any]]:
        file_ids = {item.file_id for item in submissions}
        extractions = await self.queries.extractions(file_ids, self.config.pipeline_version)
        objects = await self.queries.objects(file_ids)
        originals = await self.queries.submissions(
            item.duplicate_of for item in submissions if item.verdict == "duplicate" and item.duplicate_of
        )
        return [
            self._view(item, extractions.get(item.file_id), objects.get((item.file_id, "thumb")), originals)
            for item in submissions
        ]

    def _view(
        self,
        submission: SubmissionModel,
        extraction: ExtractionModel | None,
        thumb: StorageObjectModel | None,
        originals: dict[uuid.UUID, SubmissionModel],
    ) -> dict[str, Any]:
        reason = manager_reason(submission.verdict, reason_codes(submission))
        original = originals.get(submission.duplicate_of) if submission.duplicate_of else None
        return {
            "id": submission.id,
            "channel": submission.channel,
            "received_at": submission.received_at,
            "outcome": submission.outcome,
            "status": manager_status(submission),
            "reason_code": reason.code if reason else None,
            "reason_text": reason.text if reason else None,
            "duplicate_received_at": original.received_at if original and submission.verdict == "duplicate" else None,
            "fields": manager_fields(extraction),
            "thumb_url": self.images.link(thumb),
        }


def reset_verdict(submission: SubmissionModel) -> None:
    """Вердикт снимается; что было — остаётся в `verdict_events`."""
    submission.verdict = None
    submission.reasons = []
    submission.matches = []
    submission.duplicate_of = None
    submission.decided_at = None
    submission.audit_requested = False
    submission.audit_done_at = None
    submission.attempts = 0
    submission.last_error = None
