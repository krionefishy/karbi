import csv
import io
import uuid
from datetime import date

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.http.principals import CurrentAdmin
from backend.modules.wb_screenshot_control.application.admin_service import AdminService
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.kpi import KpiService
from backend.modules.wb_screenshot_control.infrastructure.wiring import build_staging
from backend.modules.wb_screenshot_control.presentation.http.common import (
    http_error,
    image_links,
    list_window,
    report_period,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import documented
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    AdminConfigOut,
    AdminQueuePage,
    AdminSubmissionDetail,
    AdminTelegramLinkOut,
    FileEraseOut,
    ImageKind,
    ImageUrlOut,
    ManagerDayRow,
    ManagersOut,
    PipelineOut,
    QueueKind,
    ReasonCode,
    ReprocessOut,
    ResolveRequest,
    ThresholdsOut,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/admin/screenshot-control", tags=["wb-screenshot-control-admin"])


def admin_service(request: Request, session: AsyncSession, settings: Settings) -> AdminService:
    return AdminService(session, settings.screenshot_control, image_links(request, settings), build_staging(settings))


@router.get("/queue", response_model=AdminQueuePage, responses=documented(422))
@inject
async def queue(
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    kind: QueueKind = "review",
    reason: ReasonCode | None = None,
    user_id: uuid.UUID | None = None,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> AdminQueuePage:
    """Очереди «Проверка», «Подозрения», «Аудит»: по подозрительности, затем старые первыми; «всё» — свежие первыми."""
    window = list_window(settings, date_from, date_to)
    try:
        page = await admin_service(request, session, settings).queue(
            kind, window=window, user_id=user_id, reason=reason, cursor=cursor, limit=limit
        )
    except ServiceError as error:
        raise http_error(error) from error
    return AdminQueuePage.model_validate(page)


@router.get("/submissions/{submission_id}", response_model=AdminSubmissionDetail, responses=documented(404))
@inject
async def submission(
    submission_id: uuid.UUID,
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> AdminSubmissionDetail:
    try:
        detail = await admin_service(request, session, settings).detail(submission_id)
    except ServiceError as error:
        raise http_error(error) from error
    return AdminSubmissionDetail.model_validate(detail)


@router.get("/submissions/{submission_id}/image-url", response_model=ImageUrlOut, responses=documented(404))
@inject
async def image_url(
    submission_id: uuid.UUID,
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    kind: ImageKind = "thumb",
) -> ImageUrlOut:
    """Presigned GET: оригинал как attachment, миниатюра inline. Очищенный или невыгруженный файл — available=false."""
    try:
        answer = await admin_service(request, session, settings).image_url(submission_id, kind)
    except ServiceError as error:
        raise http_error(error) from error
    return ImageUrlOut.model_validate(answer)


@router.post(
    "/submissions/{submission_id}/resolve",
    response_model=AdminSubmissionDetail,
    responses=documented(404, 409, 422),
)
@inject
async def resolve(
    submission_id: uuid.UUID,
    payload: ResolveRequest,
    request: Request,
    admin: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> AdminSubmissionDetail:
    """Решение админа по таблице переходов: недопустимое — 409 transition_not_allowed.

    Комментарий обязателен для decline и для снятия уже засчитанного (422 note_required);
    approve и not_duplicate при другом засчитанном скрине того же кейса — 409 exact_key_conflict.
    """
    try:
        detail = await admin_service(request, session, settings).resolve(
            submission_id,
            admin.user_id,
            decision=payload.decision,
            duplicate_of=payload.duplicate_of,
            outcome=payload.outcome,
            note=payload.note,
        )
    except ServiceError as error:
        raise http_error(error) from error
    return AdminSubmissionDetail.model_validate(detail)


@router.post(
    "/submissions/{submission_id}/reprocess",
    response_model=ReprocessOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=documented(404),
)
@inject
async def reprocess(
    submission_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> ReprocessOut:
    """Заново через распознавание и решение; прежний вердикт остаётся в журнале."""
    try:
        answer = await admin_service(request, session, settings).reprocess(submission_id, admin.user_id)
    except ServiceError as error:
        raise http_error(error) from error
    return ReprocessOut.model_validate(answer)


@router.delete(
    "/submissions/{submission_id}/file",
    response_model=FileEraseOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=documented(404),
)
@inject
async def erase_file(
    submission_id: uuid.UUID,
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> FileEraseOut:
    """Стереть оригинал и миниатюру досрочно: событие удаления в S3, поля и вердикты остаются."""
    try:
        answer = await admin_service(request, session, settings).erase_file(submission_id)
    except ServiceError as error:
        raise http_error(error) from error
    return FileEraseOut.model_validate(answer)


@router.get("/managers", response_model=ManagersOut, responses=documented(422))
@inject
async def managers(
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> ManagersOut:
    """Доли дублей, проверок и отказов за период; флаги — сигнал для разговора, а не санкция."""
    period = report_period(settings, date_from, date_to)
    users = await admin_service(request, session, settings).usernames()
    stats = await KpiService(session, settings.screenshot_control).managers(period, users)
    return ManagersOut.model_validate(stats)


@router.get(
    "/managers/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV `ManagerDayRow`"}, **documented(422)},
)
@inject
async def managers_export(
    request: Request,
    _: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> Response:
    """Выгрузка для HR/KPI: менеджер × день по Москве, колонки — `ManagerDayRow`. CSV через `;`, UTF-8 с BOM."""
    period = report_period(settings, date_from, date_to)
    users = await admin_service(request, session, settings).usernames()
    rows = await KpiService(session, settings.screenshot_control).daily(period, users)
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    columns = list(ManagerDayRow.model_fields)
    writer.writerow(columns)
    for row in rows:
        values = ManagerDayRow.model_validate(row).model_dump(mode="json")
        writer.writerow(
            ["true" if values[name] is True else "false" if values[name] is False else values[name] for name in columns]
        )
    filename = f"screenshot-kpi-{period.start.isoformat()}-{period.end.isoformat()}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/pipeline", response_model=PipelineOut)
@inject
async def pipeline(
    request: Request, _: CurrentAdmin, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> PipelineOut:
    answer = await admin_service(request, session, settings).pipeline(
        telegram_enabled=settings.screenshot_control.telegram_enabled
    )
    return PipelineOut.model_validate(answer)


@router.get("/config", response_model=AdminConfigOut)
@inject
async def config(_: CurrentAdmin, settings: FromDishka[Settings]) -> AdminConfigOut:
    """Действующая конфигурация без секретов: ключа HMAC и ключей S3 здесь нет."""
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
@inject
async def telegram_links(
    request: Request, _: CurrentAdmin, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> list[AdminTelegramLinkOut]:
    links = await admin_service(request, session, settings).telegram_links()
    return [AdminTelegramLinkOut.model_validate(item) for item in links]


@router.delete("/telegram-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT, responses=documented(404))
@inject
async def telegram_unlink(
    link_id: uuid.UUID,
    request: Request,
    admin: CurrentAdmin,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> Response:
    try:
        await admin_service(request, session, settings).telegram_unlink(link_id, admin.user_id)
    except ServiceError as error:
        raise http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
