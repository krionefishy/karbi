import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile, status
from redis.exceptions import RedisError

from backend.app.http.client_ip import resolve_client_ip
from backend.modules.wb_screenshot_control.domain import TOKEN_PREFIX, version_supported
from backend.modules.wb_screenshot_control.presentation.http.common import (
    accepted_submission,
    empty_summary,
    ensure_enabled,
    period_of,
    read_screenshot,
)
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error, documented, not_found
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
from backend.storage.redis import SlidingWindowRateLimiter

router = APIRouter(prefix="/wb/screenshot-control/extension", tags=["wb-screenshot-control-extension"])

BAD_TOKEN = "Токен расширения не подходит: подключите расширение заново по коду со страницы"


@dataclass(frozen=True, slots=True)
class ExtensionCaller:
    token_hash: str


async def require_extension(
    request: Request,
    authorization: str | None = Header(default=None),
    x_extension_version: str | None = Header(default=None),
) -> ExtensionCaller:
    """Токен `mas_…` установки расширения.

    Этап 1: проверяется только форма токена и версия расширения. Поиск установки по хешу,
    отзыв, простой и блокировка сотрудника (401 install_revoked, 403 user_blocked) — следующий этап.
    """
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied.startswith(TOKEN_PREFIX) or not 36 <= len(supplied) <= 128:
        raise api_error(401, "bad_token", BAD_TOKEN, headers={"WWW-Authenticate": "Bearer"})
    settings: Settings = await request.state.dishka_container.get(Settings)
    minimum = settings.screenshot_control.extension_min_version
    if not version_supported(x_extension_version, minimum):
        raise api_error(426, "upgrade_required", f"Обновите расширение до версии {minimum} или новее")
    return ExtensionCaller(token_hash=hashlib.sha256(supplied.encode()).hexdigest())


CurrentExtension = Annotated[ExtensionCaller, Depends(require_extension)]


async def limit_pairing(request: Request) -> None:
    """Код сопряжения — единственный вход без токена, поэтому жёсткий лимит с одного IP.

    Недоступный Redis — 503, как у общего лимитера: открыть перебор кодов хуже, чем отказать.
    """
    settings: Settings = await request.state.dishka_container.get(Settings)
    screens = settings.screenshot_control
    limiter = SlidingWindowRateLimiter(
        request.app.state.redis,
        limit=screens.pair_attempts_per_window,
        window_seconds=screens.pair_window_seconds,
        key_prefix="rate_limit:sc-pair",
    )
    identity = resolve_client_ip(request, behind_trusted_proxy=settings.app.trust_proxy_headers)
    try:
        decision = await limiter.check(identity)
    except (RedisError, RuntimeError) as error:
        raise api_error(503, "limiter_unavailable", "Сервис временно недоступен") from error
    if not decision.allowed:
        raise api_error(
            429,
            "rate_limited",
            "Слишком много попыток ввести код, попробуйте позже",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


@router.post(
    "/pair",
    response_model=ExtensionPairOut,
    dependencies=[Depends(limit_pairing)],
    responses=documented(404, 429, 503),
)
@inject
async def pair(payload: ExtensionPairRequest, settings: FromDishka[Settings]) -> ExtensionPairOut:
    """Обмен кода со страницы на токен установки; без авторизации, под лимитом на IP."""
    ensure_enabled(settings)
    raise api_error(404, "code_invalid", "Код не подошёл: он уже использован или устарел. Получите новый на странице")


@router.get("/me", response_model=ExtensionMeOut, responses=documented(401, 403, 426))
@inject
async def me(_: CurrentExtension, settings: FromDishka[Settings]) -> ExtensionMeOut:
    screens = settings.screenshot_control
    return ExtensionMeOut(
        username="",
        min_version=screens.extension_min_version,
        mode=screens.mode,  # type: ignore[arg-type]
        allowed_url_prefixes=list(screens.allowed_url_prefixes),
        summary=empty_summary(settings, period_of(settings, None, None)),
        server_time=datetime.now(UTC),
    )


@router.post(
    "/submissions",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
    responses=documented(401, 403, 409, 413, 415, 422, 426, 429, 503),
)
@inject
async def submit_from_extension(
    caller: CurrentExtension,
    settings: FromDishka[Settings],
    file: UploadFile = File(description="captureVisibleTab, PNG"),
    outcome: Outcome = Form(),
    client_submission_id: uuid.UUID = Form(),
    client_url: str = Form(max_length=2048, description="tab.url на момент снимка"),
    client_captured_at: datetime = Form(),
    tz_offset_min: int = Form(ge=-840, le=840),
    viewport_w: int | None = Form(default=None, ge=1, le=20000),
    viewport_h: int | None = Form(default=None, ge=1, le=20000),
    dpr: float | None = Form(default=None, gt=0, le=8),
) -> SubmissionOut:
    """Скрин из расширения; исход обязателен. Повтор того же `client_submission_id` отвечает 200."""
    ensure_enabled(settings)
    await read_screenshot(file, settings)
    return accepted_submission(caller.token_hash, client_submission_id, "extension", outcome)


@router.get("/submissions", response_model=ExtensionSubmissionList, responses=documented(401, 403, 426))
async def submissions(_: CurrentExtension, limit: int = Query(default=10, ge=1, le=50)) -> ExtensionSubmissionList:
    return ExtensionSubmissionList(items=[])


@router.get("/submissions/{submission_id}", response_model=SubmissionOut, responses=documented(401, 403, 404, 426))
async def submission(submission_id: uuid.UUID, _: CurrentExtension) -> SubmissionOut:
    """Опрос вердикта после отправки."""
    raise not_found()


@router.get("/summary", response_model=Summary, responses=documented(401, 403, 422, 426))
@inject
async def summary(
    _: CurrentExtension,
    settings: FromDishka[Settings],
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> Summary:
    return empty_summary(settings, period_of(settings, date_from, date_to))
