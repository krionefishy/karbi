import uuid
from datetime import date

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Query, Response, status

from backend.app.http.principals import CurrentAdmin
from backend.modules.wb_screenshot_control.presentation.http.common import period_of
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error, documented, not_found
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    AdminConfigOut,
    AdminQueuePage,
    AdminSubmissionDetail,
    AdminTelegramLinkOut,
    ChannelCounts,
    FileEraseOut,
    ImageKind,
    ImageUrlOut,
    ManagersOut,
    PipelineOut,
    PipelineStages,
    QueueCounts,
    QueueKind,
    ReasonCode,
    ReprocessOut,
    ResolveRequest,
    StorageStats,
    ThresholdsOut,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/admin/screenshot-control", tags=["wb-screenshot-control-admin"])


@router.get("/queue", response_model=AdminQueuePage, responses=documented(422))
@inject
async def queue(
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    kind: QueueKind = "review",
    reason: ReasonCode | None = None,
    user_id: uuid.UUID | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> AdminQueuePage:
    """Очереди «Проверка», «Подозрения», «Аудит»: сортировка по подозрительности, затем по давности."""
    if date_from or date_to:
        period_of(settings, date_from, date_to)
    return AdminQueuePage(items=[], next_cursor=None, counts=QueueCounts(review=0, duplicates=0, audit=0))


@router.get("/submissions/{submission_id}", response_model=AdminSubmissionDetail, responses=documented(404))
async def submission(submission_id: uuid.UUID, _: CurrentAdmin) -> AdminSubmissionDetail:
    raise not_found()


@router.get("/submissions/{submission_id}/image-url", response_model=ImageUrlOut, responses=documented(404))
async def image_url(submission_id: uuid.UUID, _: CurrentAdmin, kind: ImageKind = "thumb") -> ImageUrlOut:
    """Presigned GET: оригинал как attachment, миниатюра inline. Очищенный файл — available=false."""
    raise not_found()


@router.post(
    "/submissions/{submission_id}/resolve",
    response_model=AdminSubmissionDetail,
    responses=documented(404, 409, 422),
)
async def resolve(submission_id: uuid.UUID, payload: ResolveRequest, _: CurrentAdmin) -> AdminSubmissionDetail:
    """Решение админа пишется в журнал вердиктов; панель сама ничего не меняет."""
    if payload.decision == "decline" and not payload.note.strip():
        raise api_error(422, "note_required", "Укажите причину отклонения")
    raise not_found()


@router.post(
    "/submissions/{submission_id}/reprocess",
    response_model=ReprocessOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=documented(404),
)
async def reprocess(submission_id: uuid.UUID, _: CurrentAdmin) -> ReprocessOut:
    raise not_found()


@router.delete(
    "/submissions/{submission_id}/file",
    response_model=FileEraseOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=documented(404),
)
async def erase_file(submission_id: uuid.UUID, _: CurrentAdmin) -> FileEraseOut:
    """Стереть оригинал и миниатюру досрочно: событие удаления в S3, поля и вердикты остаются."""
    raise not_found()


@router.get("/managers", response_model=ManagersOut, responses=documented(422))
@inject
async def managers(
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> ManagersOut:
    return ManagersOut(period=period_of(settings, date_from, date_to), items=[])


@router.get("/pipeline", response_model=PipelineOut)
@inject
async def pipeline(_: CurrentAdmin, settings: FromDishka[Settings]) -> PipelineOut:
    screens = settings.screenshot_control
    return PipelineOut(
        mode=screens.mode,  # type: ignore[arg-type]
        telegram_enabled=screens.telegram_enabled,
        stages=PipelineStages(processing=0, extracted=0),
        oldest_processing_age_s=None,
        p50_s=None,
        p95_s=None,
        vlm_error_rate_1h=None,
        extraction_failed_today=0,
        tokens_today=0,
        storage=StorageStats(
            objects={
                "staged": 0,
                "uploading": 0,
                "stored": 0,
                "delete_requested": 0,
                "deleted": 0,
                "failed": 0,
            },
            stored_bytes=0,
            oldest_staged_age_s=None,
            staging_used_percent=None,
        ),
        channels_today=ChannelCounts(extension=0, telegram=0, web=0),
    )


@router.get("/config", response_model=AdminConfigOut)
@inject
async def config(_: CurrentAdmin, settings: FromDishka[Settings]) -> AdminConfigOut:
    screens = settings.screenshot_control
    limits = screens.thresholds
    return AdminConfigOut(
        mode=screens.mode,  # type: ignore[arg-type]
        kpi_from=date.fromisoformat(screens.kpi_from) if screens.kpi_from else None,
        telegram_enabled=screens.telegram_enabled,
        pipeline_version=screens.pipeline_version,
        vision_model=settings.vision.model or None,
        s3_bucket=screens.bucket(settings.s3),
        retention_original_days=screens.retention_original_days,
        retention_thumb_days=screens.retention_thumb_days,
        retention_fields_days=screens.retention_fields_days,
        dedupe_window_days=screens.dedupe_window_days,
        outcome_ttl_hours=screens.outcome_ttl_hours,
        review_age_max_days=screens.review_age_max_days,
        max_upload_bytes=screens.max_upload_bytes,
        min_side_px=screens.min_side_px,
        uploads_per_minute=screens.uploads_per_minute,
        uploads_per_hour=screens.uploads_per_hour,
        burst_per_hour=screens.burst_per_hour,
        audit_percent=screens.audit_percent,
        thresholds=ThresholdsOut(
            text_dup=limits.text_dup,
            text_maybe=limits.text_maybe,
            min_text_len=limits.min_text_len,
            vlm_ocr_text=limits.vlm_ocr_text,
            near_dhash_strict=limits.near_dhash_strict,
            near_tile_strict=limits.near_tile_strict,
            near_dhash_soft=limits.near_dhash_soft,
            near_tile_soft=limits.near_tile_soft,
        ),
        extension_min_version=screens.extension_min_version,
        allowed_url_prefixes=list(screens.allowed_url_prefixes),
    )


@router.get("/telegram-links", response_model=list[AdminTelegramLinkOut])
async def telegram_links(_: CurrentAdmin) -> list[AdminTelegramLinkOut]:
    return []


@router.delete("/telegram-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, responses=documented(404))
async def telegram_unlink(link_id: uuid.UUID, _: CurrentAdmin) -> Response:
    raise api_error(404, "not_found", "Привязка не найдена")
