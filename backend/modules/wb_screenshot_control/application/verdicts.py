import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.events import ScreenshotVerdictReady
from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.domain import STAGE_DECIDED
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import SubmissionModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository


def reason(code: str, severity: str = "info", **detail: Any) -> dict[str, Any]:
    return {"code": code, "severity": severity, "detail": detail}


def decide_system(
    session: AsyncSession,
    submission: SubmissionModel,
    verdict: str,
    reasons: list[dict[str, Any]],
    *,
    now: datetime,
    matches: Sequence[dict[str, Any]] = (),
    duplicate_of: uuid.UUID | None = None,
    audit_requested: bool = False,
    note: str | None = None,
) -> None:
    """Автоматический вердикт: строка, журнал (только при смене) и событие для ответа менеджеру."""
    previous = submission.verdict
    submission.verdict = verdict
    submission.auto_verdict = verdict
    submission.reasons = reasons
    submission.matches = list(matches)
    submission.duplicate_of = duplicate_of
    submission.audit_requested = audit_requested
    submission.stage = STAGE_DECIDED
    submission.decided_at = now
    submission.updated_at = now
    if previous != verdict:
        ScreenshotRepository(session).add_verdict_event(
            submission.id, actor="system", from_verdict=previous, to_verdict=verdict, reasons=reasons, note=note
        )
    publish(
        session,
        ScreenshotVerdictReady(
            submission_id=submission.id,
            user_id=submission.user_id,
            channel=submission.channel,  # type: ignore[arg-type]
            verdict=verdict,  # type: ignore[arg-type]
            previous_verdict=previous,  # type: ignore[arg-type]
            actor="system",
            decided_at=now,
        ),
    )
