from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, Response

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.platform.application import AuthenticationError, AuthService
from backend.modules.platform.presentation.http.schemas import LoginRequest, TokenResponse, UserResponse
from backend.modules.platform.presentation.http.utils import (
    REFRESH_COOKIE,
    clear_refresh_cookie,
    set_refresh_cookie,
    token_response,
    unauthorized,
    user_response,
)
from backend.shared.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
@inject
async def login(
    payload: LoginRequest,
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
) -> TokenResponse:
    try:
        session = await auth.login(payload.username, payload.password)
    except AuthenticationError as error:
        raise unauthorized() from error
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
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict[str, str]:
    await auth.logout(refresh_token)
    clear_refresh_cookie(response, settings)
    return {"status": "ok"}
