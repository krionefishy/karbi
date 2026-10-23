"""Чистка по сроку: объект переходит в `delete_requested`, удаляет его подписчик `…delete`; тексты
отзывов и реплик стираются из базы по `retention_fields_days` (`purge_texts`, работает и без S3).

Файлы: срок считается от последнего скрина с этим файлом; закреплённые в датасет файлы не трогаются,
оригинал без вердикта или на проверке ждёт решения. Повторный запуск ничего не дублирует:
выбираются только `stored`.
"""

from datetime import datetime, timedelta
from typing import Any

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


TEXT_KEYS = ("review_text", "text_norm")
"""Поля распознавания с текстом отзыва; реплики переписки стираются отдельно (`messages[].text_norm`)."""


def without_texts(fields: dict[str, Any]) -> dict[str, Any]:
    """Поля распознавания без текстов: время, звёзды, chatId, артикул, HMAC имени и флаги остаются."""
    stripped = {key: (None if key in TEXT_KEYS else value) for key, value in fields.items()}
    messages = fields.get("messages")
    if isinstance(messages, list):
        stripped["messages"] = [
            {**message, "text_norm": ""} if isinstance(message, dict) else message for message in messages
        ]
    stripped["texts_purged"] = True
    return stripped


async def purge_texts(database: Database, config: ScreenshotControlConfig, now: datetime) -> dict[str, int]:
    """Срок `retention_fields_days` (3 года) для текстов: отзывы и реплики покупателей — ПДн.

    У сабмишенов стираются `text_norm`, MinHash и подпись, у распознаваний — тексты в `fields` и сырые
    ответы `vlm_json`/`ocr_json`. Вердикты, исходы, журнал, ключи кейса и длина текста остаются: KPI за
    прошлые периоды от самих текстов не зависит. Срок всегда длиннее окна
    дедупа (`dedupe_window_days`, проверяется настройками), поэтому кандидатов для новых скринов это
    не трогает. Повторный запуск безопасен: выбираются только строки, где тексты ещё есть.
    """
    cutoff = now - timedelta(days=config.retention_fields_days)
    counts = {"submissions": 0, "extractions": 0}
    while True:
        async with database.session() as session:
            stripped = await ScreenshotRepository(session).strip_submission_texts(cutoff, BATCH)
            await session.commit()
        counts["submissions"] += stripped
        if stripped < BATCH:
            break
    while True:
        async with database.session() as session:
            rows = await ScreenshotRepository(session).extractions_with_texts(cutoff, BATCH)
            for extraction in rows:
                extraction.vlm_json = None
                extraction.ocr_json = None
                if extraction.fields is not None:
                    extraction.fields = without_texts(extraction.fields)
            await session.commit()
        counts["extractions"] += len(rows)
        if len(rows) < BATCH:
            break
    return counts
