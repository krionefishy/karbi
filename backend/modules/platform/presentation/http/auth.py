from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, Request, Response

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.platform.application import (
    AuthenticationError,
    AuthService,
    LoginAttemptLimiter,
    LoginRateLimitError,
    UserAdminService,
)
from backend.modules.platform.presentation.http.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    TokenResponse,
    UserResponse,
)
from backend.modules.platform.presentation.http.utils import (
    REFRESH_COOKIE,
    clear_refresh_cookie,
    set_refresh_cookie,
    token_response,
    too_many_login_attempts,
    unauthorized,
    user_response,
)
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@inject
async def login(
    payload: LoginRequest,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    redis: FromDishka[RedisClient],
) -> TokenResponse:
    attempts = LoginAttemptLimiter(redis)
    try:
        await attempts.ensure_allowed(payload.username)
    except LoginRateLimitError as error:
        raise too_many_login_attempts(error.retry_after_seconds) from error
    try:
        session = await auth.login(payload.username, payload.password)
    except AuthenticationError as error:
        await attempts.record_failure(payload.username)
        raise unauthorized() from error
    await attempts.reset(payload.username)
    set_refresh_cookie(response, session.refresh_token, settings)
    return token_response(session, settings)


@router.post("/refresh", response_model=TokenResponse)
@inject
async def refresh(
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    if not refresh_token:
        raise unauthorized()
    try:
        session = await auth.refresh(refresh_token)
    except AuthenticationError as error:
        clear_refresh_cookie(response, settings)
        raise unauthorized() from error
    set_refresh_cookie(response, session.refresh_token, settings)
    return token_response(session, settings)


@router.get("/me", response_model=UserResponse)
@inject
async def me(
    auth: FromDishka[AuthService],
    principal: CurrentPrincipal,
) -> UserResponse:
    try:
        user = await auth.current_user(principal.user_id)
    except AuthenticationError as error:
        raise unauthorized() from error
    return user_response(user)


@router.post("/logout")
@inject
async def logout(
    request: Request,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict[str, str]:
    scheme, _, access_token = request.headers.get("authorization", "").partition(" ")
    await auth.logout(refresh_token, access_token.strip() if scheme.lower() == "bearer" else None)
    clear_refresh_cookie(response, settings)
    return {"status": "ok"}


@router.post("/password")
@inject
async def change_password(
    payload: PasswordChangeRequest,
    response: Response,
    principal: CurrentPrincipal,
    service: FromDishka[UserAdminService],
) -> dict[str, str]:
    """Change your own password. Other sessions keep working: refresh tokens are
    stored by their own hash, so there is no way to find the rest of them."""
    try:
        await service.change_own_password(principal.user_id, payload.current_password, payload.new_password)
    except AuthenticationError as error:
        raise unauthorized() from error
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
