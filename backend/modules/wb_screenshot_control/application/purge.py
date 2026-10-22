"""Чистка по сроку: объект переходит в `delete_requested`, удаляет его подписчик `…delete`.

Срок считается от последнего скрина с этим файлом; закреплённые в датасет файлы не трогаются,
оригинал без вердикта или на проверке ждёт решения. Повторный запуск ничего не дублирует:
выбираются только `stored`.
"""

from datetime import datetime, timedelta

from backend.modules.wb_screenshot_control.application.outbox import publish
from backend.modules.wb_screenshot_control.application.storage import delete_event
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig
from backend.storage.pg import Database

BATCH = 500


async def purge_expired(database: Database, config: ScreenshotControlConfig, now: datetime) -> dict[str, int]:
    requested: dict[str, int] = {}
    for kind, days in (("orig", config.retention_original_days), ("thumb", config.retention_thumb_days)):
        count = 0
        while True:
            async with database.session() as session:
                expired = await ScreenshotRepository(session).expired_objects(
                    kind, now - timedelta(days=days), BATCH, keep_unresolved=kind == "orig"
                )
                for stored in expired:
                    stored.state = "delete_requested"
                    stored.delete_requested_at = now
                    stored.updated_at = now
                    publish(session, delete_event(stored, "retention"))
                await session.commit()
            count += len(expired)
            if len(expired) < BATCH:
                break
        requested[kind] = count
    return requested
