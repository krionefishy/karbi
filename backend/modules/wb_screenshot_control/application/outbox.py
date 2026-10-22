"""События модуля в общий outbox. Отложенное событие — та же строка с `next_attempt_at` в будущем:
публикатор не возьмёт её раньше срока, и консьюмер не крутится вхолостую.
"""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.modules.wb_screenshot_control.application.events import (
    DECIDE_PARTITION_KEY,
    EVENT_DELETE,
    EVENT_EXTRACTED,
    EVENT_SUBMITTED,
    EVENT_UPLOAD,
    EVENT_VERDICT_READY,
    ScreenshotExtracted,
    ScreenshotObjectDelete,
    ScreenshotObjectUpload,
    ScreenshotSubmitted,
    ScreenshotVerdictReady,
)
from backend.shared.kafka_streams.topics import ScreenshotControlTopics, StorageTopics

type ModuleEvent = (
    ScreenshotSubmitted | ScreenshotExtracted | ScreenshotVerdictReady | ScreenshotObjectUpload | ScreenshotObjectDelete
)

_ROUTES: dict[type, tuple[str, str]] = {
    ScreenshotSubmitted: (EVENT_SUBMITTED, ScreenshotControlTopics.SUBMITTED),
    ScreenshotExtracted: (EVENT_EXTRACTED, ScreenshotControlTopics.EXTRACTED),
    ScreenshotVerdictReady: (EVENT_VERDICT_READY, ScreenshotControlTopics.VERDICT_READY),
    ScreenshotObjectUpload: (EVENT_UPLOAD, StorageTopics.WB_SCREENSHOT_CONTROL_UPLOAD),
    ScreenshotObjectDelete: (EVENT_DELETE, StorageTopics.WB_SCREENSHOT_CONTROL_DELETE),
}


def _aggregate(event: ModuleEvent) -> uuid.UUID:
    if isinstance(event, ScreenshotExtracted):
        return DECIDE_PARTITION_KEY
    if isinstance(event, ScreenshotObjectUpload | ScreenshotObjectDelete):
        return event.file_id
    return event.submission_id


def publish(session: AsyncSession, event: ModuleEvent, *, not_before: datetime | None = None) -> uuid.UUID:
    event_type, topic = _ROUTES[type(event)]
    event_id = uuid.uuid4()
    aggregate = _aggregate(event)
    model = OutboxEventModel(
        id=event_id,
        aggregate_id=aggregate,
        event_type=event_type,
        topic=topic,
        message_key=str(aggregate),
        payload={**event.payload(), "event_id": str(event_id)},
    )
    if not_before is not None:
        model.next_attempt_at = not_before
    session.add(model)
    return event_id
