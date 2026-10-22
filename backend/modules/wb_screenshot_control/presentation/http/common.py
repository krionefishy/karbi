"""Общее для маршрутов менеджера, расширения и админа.

Этап 1 — каркас: ответы канонные по форме, но данных за ними ещё нет (списки пустые, счётчики
нулевые, конкретный скриншот — 404). Приём уже проверяет то, что проверит и боевой код:
размер, тип по содержимому, период, режим модуля.
"""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi import UploadFile

from backend.modules.wb_screenshot_control.application import Period as ReportPeriod
from backend.modules.wb_screenshot_control.application import PeriodError, preliminary, resolve_period
from backend.modules.wb_screenshot_control.domain import (
    IMAGE_TYPES,
    OUTCOME_CAPTIONS,
    OUTCOME_LABELS,
    OUTCOMES,
    sniff_image_type,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    Channel,
    ManagerConfigOut,
    Outcome,
    OutcomeCounts,
    OutcomeOption,
    Period,
    SubmissionOut,
    Summary,
)
from backend.shared.settings import Settings


def ensure_enabled(settings: Settings) -> None:
    if settings.screenshot_control.mode == "off":
        raise api_error(503, "module_off", "Контроль скриншотов выключен")


def period_of(settings: Settings, start: date | None, end: date | None) -> Period:
    today = datetime.now(ZoneInfo(settings.screenshot_control.timezone)).date()
    try:
        resolved = resolve_period(start, end, today=today)
    except PeriodError as error:
        raise api_error(422, "period_too_long", str(error)) from error
    return Period.model_validate({"from": resolved.start, "to": resolved.end})


def empty_summary(settings: Settings, period: Period) -> Summary:
    screens = settings.screenshot_control
    return Summary(
        period=period,
        mode=screens.mode,  # type: ignore[arg-type]
        preliminary=preliminary(screens.mode, ReportPeriod(period.from_, period.to), screens.kpi_from),
        counts=OutcomeCounts(positive=0, amended=0, deleted=0, accepted_total=0),
        pending_review=0,
        awaiting_outcome=0,
        processing=0,
        duplicates=0,
        rejected=0,
    )


def manager_config(settings: Settings) -> ManagerConfigOut:
    screens = settings.screenshot_control
    return ManagerConfigOut(
        mode=screens.mode,  # type: ignore[arg-type]
        telegram_enabled=screens.telegram_enabled,
        outcomes=[
            OutcomeOption(value=value, caption=OUTCOME_CAPTIONS[value], label=OUTCOME_LABELS[value])  # type: ignore[arg-type]
            for value in OUTCOMES
        ],
        max_upload_bytes=screens.max_upload_bytes,
        accepted_types=list(IMAGE_TYPES),
        min_side_px=screens.min_side_px,
        extension_url=screens.public_base_url.rstrip("/") + screens.extension_download_path,
        extension_min_version=screens.extension_min_version,
        bot_username=None,
    )


async def read_screenshot(upload: UploadFile, settings: Settings) -> tuple[bytes, str]:
    limit = settings.screenshot_control.max_upload_bytes
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise api_error(413, "file_too_large", f"Файл больше {limit // (1024 * 1024)} МБ")
    content_type = sniff_image_type(data[:16])
    if content_type is None:
        raise api_error(415, "unsupported_type", "Нужен скриншот в PNG, JPEG или WEBP")
    return data, content_type


def accepted_submission(
    user_key: str, client_submission_id: uuid.UUID, channel: Channel, outcome: Outcome | None
) -> SubmissionOut:
    """Канонный ответ приёма: тот же client_submission_id даёт тот же id."""
    return SubmissionOut(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"wb-screenshot-control:{user_key}:{client_submission_id}"),
        channel=channel,
        received_at=datetime.now(UTC),
        outcome=outcome,
        status="processing" if outcome else "awaiting_outcome",
        reason_code=None,
        reason_text=None,
        duplicate_received_at=None,
        fields=None,
        thumb_url=None,
    )
