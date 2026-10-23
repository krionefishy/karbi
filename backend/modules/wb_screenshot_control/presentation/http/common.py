"""Общее для маршрутов менеджера, расширения и админа: режим, периоды, ошибки, сборка сервисов."""

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application import Period as ReportPeriod
from backend.modules.wb_screenshot_control.application import PeriodError, resolve_period
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.images import ImageLinks
from backend.modules.wb_screenshot_control.application.kpi import window_of
from backend.modules.wb_screenshot_control.application.submissions_service import SubmissionsService
from backend.modules.wb_screenshot_control.domain import (
    IMAGE_TYPES,
    OUTCOME_CAPTIONS,
    OUTCOME_LABELS,
    OUTCOMES,
    sniff_image_type,
)
from backend.modules.wb_screenshot_control.domain.ports import BlobStore
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import Window
from backend.modules.wb_screenshot_control.infrastructure.wiring import build_blob_store
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    ManagerConfigOut,
    OutcomeOption,
    Period,
)
from backend.shared.settings import Settings

# Код ошибки контракта → HTTP-статус. Сервисы знают только код.
STATUS_BY_CODE: dict[str, int] = {
    "not_found": 404,
    "file_too_large": 413,
    "unsupported_type": 415,
    "too_small": 422,
    "bad_image": 422,
    "bad_outcome": 422,
    "outcome_required": 422,
    "idempotency_conflict": 409,
    "outcome_locked": 409,
    "period_too_long": 422,
    "storage_full": 503,
    "rate_limited": 429,
    "limiter_unavailable": 503,
    "module_off": 503,
    "code_invalid": 404,
    "bad_token": 401,
    "install_revoked": 401,
    "user_blocked": 403,
    "upgrade_required": 426,
    "exact_key_conflict": 409,
    "note_required": 422,
    "duplicate_target_invalid": 422,
    "telegram_disabled": 503,
    "transition_not_allowed": 409,
    "bad_cursor": 422,
}
_BEARER = {"bad_token", "install_revoked"}


def http_error(error: ServiceError) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if error.code in _BEARER else None
    return api_error(STATUS_BY_CODE[error.code], error.code, error.message, headers=headers)  # type: ignore[arg-type]


def ensure_enabled(settings: Settings) -> None:
    screens = settings.screenshot_control
    if screens.mode == "off":
        if screens.disabled_reason == "hmac_key_missing":
            raise api_error(503, "module_off", "Контроль скриншотов выключен: на сервере не задан ключ HMAC")
        raise api_error(503, "module_off", "Контроль скриншотов выключен")


def report_period(settings: Settings, start: date | None, end: date | None) -> ReportPeriod:
    today = datetime.now(ZoneInfo(settings.screenshot_control.timezone)).date()
    try:
        return resolve_period(start, end, today=today)
    except PeriodError as error:
        raise api_error(422, "period_too_long", str(error)) from error


def period_of(settings: Settings, start: date | None, end: date | None) -> Period:
    resolved = report_period(settings, start, end)
    return Period.model_validate({"from": resolved.start, "to": resolved.end})


def list_window(settings: Settings, start: date | None, end: date | None) -> Window:
    """Для списков: без дат — без границы; с датами — дни по Москве, как у сводки."""
    if start is None and end is None:
        return Window()
    return window_of(report_period(settings, start, end), settings.screenshot_control.timezone)


def blob_store(request: Request, settings: Settings) -> BlobStore | None:
    """Клиент S3 один на приложение; тесты подкладывают свой в `app.state.screenshot_blobs`."""
    state = request.app.state
    if not hasattr(state, "screenshot_blobs"):
        state.screenshot_blobs = build_blob_store(settings)
    blobs: BlobStore | None = state.screenshot_blobs
    return blobs


def image_links(request: Request, settings: Settings) -> ImageLinks:
    return ImageLinks(blob_store(request, settings), settings.screenshot_control.image_url_ttl_seconds)


def submissions_service(request: Request, session: AsyncSession, settings: Settings) -> SubmissionsService:
    return SubmissionsService(session, settings.screenshot_control, image_links(request, settings))


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


def client_meta(request: Request, **values: Any) -> dict[str, Any]:
    """Что клиент рассказал о себе; пустое не пишется."""
    meta = {"user_agent": (request.headers.get("user-agent") or "")[:300] or None, **values}
    return {key: value for key, value in meta.items() if value is not None}
