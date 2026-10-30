import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.application.extension_service import ExtensionCaller, ExtensionService
from backend.modules.wb_screenshot_control.application.kpi import KpiService
from backend.modules.wb_screenshot_control.application.settings_service import user_timezone
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import Window
from backend.modules.wb_screenshot_control.infrastructure.wiring import build_ingest
from backend.modules.wb_screenshot_control.presentation.http.common import (
    client_meta,
    ensure_enabled,
    http_error,
    read_screenshot,
    report_period,
    submissions_service,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error, documented
from backend.modules.wb_screenshot_control.presentation.http.limits import limit_pairing, limit_user
from backend.modules.wb_screenshot_control.presentation.http.schemas import (
    ExtensionMeOut,
    ExtensionPairOut,
    ExtensionPairRequest,
    ExtensionSubmissionList,
    Outcome,
    SubmissionOut,
    Summary,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/wb/screenshot-control/extension", tags=["wb-screenshot-control-extension"])


async def require_extension(
    request: Request,
    authorization: str | None = Header(default=None),
    x_extension_version: str | None = Header(default=None),
) -> ExtensionCaller:
    """Токен `mas_…` установки: не отозван, не простаивал дольше срока, сотрудник активен, версия не ниже минимальной.

    401 bad_token / install_revoked — расширение стирает токен и просит подключиться заново;
    403 user_blocked; 426 upgrade_required. При mode=off после проверки токена — 503 module_off.
    """
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    container = request.state.dishka_container
    settings: Settings = await container.get(Settings)
    session: AsyncSession = await container.get(AsyncSession)
    try:
        caller = await ExtensionService(session, settings.screenshot_control).authenticate(
            supplied, x_extension_version
        )
    except ServiceError as error:
        raise http_error(error) from error
    ensure_enabled(settings)
    return caller


CurrentExtension = Annotated[ExtensionCaller, Depends(require_extension)]


@router.post(
    "/pair",
    response_model=ExtensionPairOut,
    dependencies=[Depends(limit_pairing)],
    responses=documented(404, 426, 429, 503),
)
@inject
async def pair(
    payload: ExtensionPairRequest, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> ExtensionPairOut:
    """Обмен кода со страницы на токен установки; без авторизации, под жёстким лимитом на IP."""
    ensure_enabled(settings)
    try:
        paired = await ExtensionService(session, settings.screenshot_control).pair(
            payload.code, install_id=payload.install_id, browser=payload.browser, ext_version=payload.ext_version
        )
    except ServiceError as error:
        raise http_error(error) from error
    return ExtensionPairOut.model_validate(paired)


@router.get("/me", response_model=ExtensionMeOut, responses=documented(401, 403, 426, 429))
@inject
async def me(
    request: Request, caller: CurrentExtension, settings: FromDishka[Settings], session: FromDishka[AsyncSession]
) -> ExtensionMeOut:
    await limit_user(request, settings, caller.user_id, "poll")
    screens = settings.screenshot_control
    kpi = KpiService(session, screens)
    return ExtensionMeOut(
        username=caller.username,
        min_version=screens.extension_min_version,
        mode=screens.mode,  # type: ignore[arg-type]
        allowed_url_prefixes=list(screens.allowed_url_prefixes),
        summary=Summary.model_validate(await kpi.summary(caller.user_id, report_period(settings, None, None))),
        server_time=datetime.now(UTC),
        timezone=await user_timezone(session, caller.user_id, screens),
    )


@router.post(
    "/submissions",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
    responses=documented(401, 403, 409, 413, 415, 422, 426, 429, 503),
)
@inject
async def submit_from_extension(
    request: Request,
    response: Response,
    caller: CurrentExtension,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    file: UploadFile = File(description="captureVisibleTab, PNG"),
    outcome: Outcome | None = Form(default=None, description="Обязателен: без него 422 outcome_required"),
    client_submission_id: uuid.UUID = Form(),
    client_url: str = Form(max_length=2048, description="tab.url на момент снимка"),
    client_captured_at: datetime = Form(),
    tz_offset_min: int = Form(ge=-840, le=840),
    viewport_w: int | None = Form(default=None, ge=1, le=20000),
    viewport_h: int | None = Form(default=None, ge=1, le=20000),
    dpr: float | None = Form(default=None, gt=0, le=8),
) -> SubmissionOut:
    """Скрин из расширения; исход обязателен. Повтор того же `client_submission_id` отвечает 200."""
    if outcome is None:
        raise api_error(422, "outcome_required", "Выберите исход: полож, доп или удален")
    await limit_user(request, settings, caller.user_id, "upload")
    data, _ = await read_screenshot(file, settings)
    meta = client_meta(request, ext_version=caller.ext_version, viewport_w=viewport_w, viewport_h=viewport_h, dpr=dpr)
    try:
        view, created = await submissions_service(request, session, settings).submit(
            build_ingest(session, settings),
            caller.user_id,
            "extension",
            data,
            client_submission_id=str(client_submission_id),
            outcome=outcome,
            outcome_source="button",
            client_url=client_url,
            client_captured_at=client_captured_at,
            tz_offset_min=tz_offset_min,
            client_meta=meta,
            install_id=caller.install_id,
        )
    except ServiceError as error:
        raise http_error(error) from error
    if not created:
        response.status_code = status.HTTP_200_OK
    return SubmissionOut.model_validate(view)


@router.get("/submissions", response_model=ExtensionSubmissionList, responses=documented(401, 403, 426, 429))
@inject
async def submissions(
    request: Request,
    caller: CurrentExtension,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    limit: int = Query(default=10, ge=1, le=50),
) -> ExtensionSubmissionList:
    """Последние скрины, отправленные расширением."""
    await limit_user(request, settings, caller.user_id, "poll")
    page = await submissions_service(request, session, settings).page(
        caller.user_id, window=Window(), channel="extension", limit=limit
    )
    return ExtensionSubmissionList.model_validate({"items": page["items"]})


@router.get("/submissions/{submission_id}", response_model=SubmissionOut, responses=documented(401, 403, 404, 426, 429))
@inject
async def submission(
    submission_id: uuid.UUID,
    request: Request,
    caller: CurrentExtension,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
) -> SubmissionOut:
    """Опрос вердикта после отправки; чужой скрин — 404."""
    await limit_user(request, settings, caller.user_id, "poll")
    try:
        view = await submissions_service(request, session, settings).card(caller.user_id, submission_id)
    except ServiceError as error:
        raise http_error(error) from error
    return SubmissionOut.model_validate(view)


@router.get("/summary", response_model=Summary, responses=documented(401, 403, 422, 426, 429))
@inject
async def summary(
    request: Request,
    caller: CurrentExtension,
    settings: FromDishka[Settings],
    session: FromDishka[AsyncSession],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> Summary:
    period = report_period(settings, date_from, date_to)
    await limit_user(request, settings, caller.user_id, "poll")
    kpi = KpiService(session, settings.screenshot_control)
    return Summary.model_validate(await kpi.summary(caller.user_id, period))
