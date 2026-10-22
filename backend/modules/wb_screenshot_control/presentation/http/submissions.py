import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_screenshot_control.domain import PAIRING_ALPHABET
from backend.modules.wb_screenshot_control.presentation.http.common import (
    accepted_submission,
    empty_summary,
    ensure_enabled,
    manager_config,
    period_of,
    read_screenshot,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error, documented, not_found
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
    principal: CurrentPrincipal,
    settings: FromDishka[Settings],
    file: UploadFile = File(description="PNG, JPEG или WEBP"),
    client_submission_id: uuid.UUID = Form(description="uuid на файл; повтор с тем же id вернёт ту же запись"),
    outcome: Outcome | None = Form(default=None),
    client_captured_at: datetime | None = Form(default=None),
) -> SubmissionOut:
    """Веб-загрузка. Повтор того же `client_submission_id` отвечает 200 той же записью."""
    ensure_enabled(settings)
    await read_screenshot(file, settings)
    return accepted_submission(str(principal.user_id), client_submission_id, "web", outcome)


@router.get("/submissions", response_model=SubmissionPage, responses=documented(422))
@inject
async def submissions(
    _: CurrentPrincipal,
    settings: FromDishka[Settings],
    status_: Status | None = Query(default=None, alias="status"),
    outcome: Outcome | None = None,
    date_from: date | None = Query(default=None, alias="from", description="Дата по Москве"),
    date_to: date | None = Query(default=None, alias="to", description="Дата по Москве, включительно"),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
) -> SubmissionPage:
    period_of(settings, date_from, date_to)
    return SubmissionPage(items=[], next_cursor=None)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut, responses=documented(404))
async def submission(submission_id: uuid.UUID, _: CurrentPrincipal) -> SubmissionOut:
    """Только свой скриншот: чужой отвечает тем же 404, что и несуществующий."""
    raise not_found()


@router.patch("/submissions/{submission_id}/outcome", response_model=SubmissionOut, responses=documented(404, 409))
async def change_outcome(submission_id: uuid.UUID, payload: OutcomeUpdate, _: CurrentPrincipal) -> SubmissionOut:
    """Исход меняется, пока нет вердикта; после — 409 outcome_locked, дальше только админ."""
    raise not_found()


@router.get("/submissions/{submission_id}/image-url", response_model=ImageUrlOut, responses=documented(404))
async def image_url(submission_id: uuid.UUID, _: CurrentPrincipal, kind: Literal["thumb"] = "thumb") -> ImageUrlOut:
    """Presigned GET миниатюры из S3; оригинал менеджеру не отдаётся."""
    raise not_found()


@router.get("/summary", response_model=Summary, responses=documented(422))
@inject
async def summary(
    _: CurrentPrincipal,
    settings: FromDishka[Settings],
    date_from: date | None = Query(default=None, alias="from", description="Дата по Москве, по умолчанию сегодня"),
    date_to: date | None = Query(default=None, alias="to"),
) -> Summary:
    return empty_summary(settings, period_of(settings, date_from, date_to))


@router.get("/config", response_model=ManagerConfigOut)
@inject
async def config(_: CurrentPrincipal, settings: FromDishka[Settings]) -> ManagerConfigOut:
    return manager_config(settings)


@router.post("/extension/pairing-code", response_model=PairingCodeOut, responses=documented(429, 503))
@inject
async def pairing_code(_: CurrentPrincipal, settings: FromDishka[Settings]) -> PairingCodeOut:
    """Новый код гасит прежний живой код сотрудника."""
    ensure_enabled(settings)
    code = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    ttl = timedelta(minutes=settings.screenshot_control.pairing_ttl_minutes)
    return PairingCodeOut(code=code, expires_at=datetime.now(UTC) + ttl)


@router.get("/extension/installs", response_model=list[ExtensionInstallOut])
async def installs(_: CurrentPrincipal) -> list[ExtensionInstallOut]:
    return []


@router.delete("/extension/installs/{install_id}", status_code=status.HTTP_204_NO_CONTENT, responses=documented(404))
async def revoke_install(install_id: uuid.UUID, _: CurrentPrincipal) -> Response:
    """Отозвать можно только свою установку."""
    raise not_found()


@router.post("/telegram/link-token", response_model=TelegramLinkOut, responses=documented(503))
@inject
async def telegram_link_token(_: CurrentPrincipal, settings: FromDishka[Settings]) -> TelegramLinkOut:
    raise api_error(503, "telegram_disabled", "Telegram-бот подключается во втором релизе")


@router.get("/telegram", response_model=TelegramStatusOut)
@inject
async def telegram_status(_: CurrentPrincipal, settings: FromDishka[Settings]) -> TelegramStatusOut:
    return TelegramStatusOut(
        enabled=settings.screenshot_control.telegram_enabled, linked=False, username=None, linked_at=None
    )


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def telegram_unlink(_: CurrentPrincipal) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)
