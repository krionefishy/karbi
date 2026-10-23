import uuid
from datetime import UTC, date, datetime
from typing import Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.extension_service import ExtensionService
from backend.modules.wb_screenshot_control.application.kpi import KpiService
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import TelegramLinks
from backend.modules.wb_screenshot_control.infrastructure.wiring import build_ingest
from backend.modules.wb_screenshot_control.presentation.http.common import (
    client_meta,
    ensure_enabled,
    http_error,
    list_window,
    manager_config,
    read_screenshot,
    report_period,
    submissions_service,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error, documented
from backend.modules.wb_screenshot_control.presentation.http.limits import limit_user
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    ExtensionInstallOut,
    ImageUrlOut,
    ManagerConfigOut,
    Outcome,
    OutcomeUpdate,
    PairingCodeOut,
    Status,
    SubmissionOut,
    SubmissionPage,
    Summary,
    TelegramLinkOut,
    TelegramStatusOut,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/wb/screenshot-control", tags=["wb-screenshot-control"])


@router.post(
    "/submissions",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
    responses=documented(409, 413, 415, 422, 429, 503),
)
@inject
async def submit_screenshot(
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    file: UploadFile = File(description="PNG, JPEG или WEBP"),
    client_submission_id: uuid.UUID = Form(description="uuid на файл; повтор с тем же id вернёт ту же запись"),
    outcome: Outcome | None = Form(default=None),
    client_captured_at: datetime | None = Form(default=None),
) -> SubmissionOut:
    """Веб-загрузка. Повтор того же `client_submission_id` отвечает 200 той же записью."""
    ensure_enabled(settings)
    await limit_user(request, settings, principal.user_id, "upload")
    data, _ = await read_screenshot(file, settings)
    try:
        view, created = await submissions_service(request, session, settings).submit(
            build_ingest(session, settings),
            principal.user_id,
            "web",
            data,
            client_submission_id=str(client_submission_id),
            outcome=outcome,
            outcome_source="form" if outcome else None,
            client_captured_at=client_captured_at,
            client_meta=client_meta(request),
        )
    except ServiceError as error:
        raise http_error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return SubmissionOut.model_validate(view)


@router.get("/submissions", response_model=SubmissionPage, responses=documented(422, 429))
@inject
async def submissions(
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    status_: Status | None = Query(default=None, alias="status"),
    outcome: Outcome | None = None,
    date_from: date | None = Query(default=None, alias="from", description="Дата по Москве"),
    date_to: date | None = Query(default=None, alias="to", description="Дата по Москве, включительно"),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> SubmissionPage:
    """Свои скриншоты, свежие первыми. Без дат — за всё время."""
    ensure_enabled(settings)
    await limit_user(request, settings, principal.user_id, "poll")
    window = list_window(settings, date_from, date_to)
    try:
        page = await submissions_service(request, session, settings).page(
            principal.user_id, window=window, status=status_, outcome=outcome, cursor=cursor, limit=limit
        )
    except ServiceError as error:
        raise http_error(error) from error
    return SubmissionPage.model_validate(page)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut, responses=documented(404, 429))
@inject
async def submission(
    submission_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> SubmissionOut:
    """Только свой скриншот: чужой отвечает тем же 404, что и несуществующий."""
    ensure_enabled(settings)
    await limit_user(request, settings, principal.user_id, "poll")
    try:
        view = await submissions_service(request, session, settings).card(principal.user_id, submission_id)
    except ServiceError as error:
        raise http_error(error) from error
    return SubmissionOut.model_validate(view)


@router.patch("/submissions/{submission_id}/outcome", response_model=SubmissionOut, responses=documented(404, 409))
@inject
async def change_outcome(
    submission_id: uuid.UUID,
    payload: OutcomeUpdate,
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> SubmissionOut:
    """Исход меняется, пока нет вердикта; после — 409 outcome_locked, дальше только админ.

    Отказ «не выбран исход» в пределах `outcome_ttl_hours` исправляется так же: решение пересчитается.
    """
    ensure_enabled(settings)
    try:
        view = await submissions_service(request, session, settings).change_outcome(
            principal.user_id, submission_id, payload.outcome
        )
    except ServiceError as error:
        raise http_error(error) from error
    return SubmissionOut.model_validate(view)


@router.get("/submissions/{submission_id}/image-url", response_model=ImageUrlOut, responses=documented(404, 429))
@inject
async def image_url(
    submission_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    kind: Literal["thumb"] = "thumb",
) -> ImageUrlOut:
    """Presigned GET миниатюры из S3; оригинал менеджеру не отдаётся. Пока файла в S3 нет — available=false."""
    ensure_enabled(settings)
    await limit_user(request, settings, principal.user_id, "poll")
    try:
        answer = await submissions_service(request, session, settings).image_url(principal.user_id, submission_id)
    except ServiceError as error:
        raise http_error(error) from error
    return ImageUrlOut.model_validate(answer)


@router.get("/summary", response_model=Summary, responses=documented(422, 429))
@inject
async def summary(
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    date_from: date | None = Query(default=None, alias="from", description="Дата по Москве, по умолчанию сегодня"),
    date_to: date | None = Query(default=None, alias="to"),
) -> Summary:
    ensure_enabled(settings)
    period = report_period(settings, date_from, date_to)
    await limit_user(request, settings, principal.user_id, "poll")
    kpi = KpiService(session, settings.screenshot_control)
    return Summary.model_validate(await kpi.summary(principal.user_id, period))


@router.get("/config", response_model=ManagerConfigOut)
@inject
async def config(_: CurrentPrincipal, settings: FromDishka[Settings]) -> ManagerConfigOut:
    """Отвечает и при mode=off: по нему страница понимает, что модуль выключен."""
    return manager_config(settings)


@router.post("/extension/pairing-code", response_model=PairingCodeOut, responses=documented(429, 503))
@inject
async def pairing_code(
    request: Request,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> PairingCodeOut:
    """Новый код гасит прежний живой код сотрудника."""
    ensure_enabled(settings)
    await limit_user(request, settings, principal.user_id, "pairing_code")
    code = await ExtensionService(session, settings.screenshot_control).pairing_code(principal.user_id)
    return PairingCodeOut.model_validate(code)


@router.get("/extension/installs", response_model=list[ExtensionInstallOut])
@inject
async def installs(
    principal: CurrentPrincipal, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> list[ExtensionInstallOut]:
    ensure_enabled(settings)
    found = await ExtensionService(session, settings.screenshot_control).installs(principal.user_id)
    return [ExtensionInstallOut.model_validate(item) for item in found]


@router.delete("/extension/installs/{install_id}", status_code=status.HTTP_204_NO_CONTENT, responses=documented(404))
@inject
async def revoke_install(
    install_id: uuid.UUID,
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> Response:
    """Отозвать можно только свою установку: чужая отвечает 404."""
    ensure_enabled(settings)
    try:
        await ExtensionService(session, settings.screenshot_control).revoke(principal.user_id, install_id)
    except ServiceError as error:
        raise http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/telegram/link-token", response_model=TelegramLinkOut, responses=documented(503))
@inject
async def telegram_link_token(_: CurrentPrincipal, settings: FromDishka[Settings]) -> TelegramLinkOut:
    """Telegram — второй релиз: пока бот не подключён, привязка недоступна при любом флаге."""
    ensure_enabled(settings)
    raise api_error(503, "telegram_disabled", "Telegram-бот подключается во втором релизе")


@router.get("/telegram", response_model=TelegramStatusOut)
@inject
async def telegram_status(
    principal: CurrentPrincipal, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> TelegramStatusOut:
    ensure_enabled(settings)
    links = await TelegramLinks(session).live(principal.user_id)
    link = links[0] if links else None
    return TelegramStatusOut(
        enabled=settings.screenshot_control.telegram_enabled,
        linked=link is not None,
        username=link.username or None if link else None,
        linked_at=link.linked_at if link else None,
    )


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def telegram_unlink(
    principal: CurrentPrincipal, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> Response:
    ensure_enabled(settings)
    await TelegramLinks(session).revoke(datetime.now(UTC), by=principal.user_id, user_id=principal.user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
