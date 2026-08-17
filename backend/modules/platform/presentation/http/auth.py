from typing import Annotated, Literal

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Cookie, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from backend.modules.platform.application import AuthenticationError, AuthService, AuthSession
from backend.modules.platform.domain import User
from backend.shared.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE = "karbi_refresh"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    username: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


def _user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username)


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.auth.refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.app.environment in {"production", "prod"},
        samesite="lax",
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        key=REFRESH_COOKIE,
        httponly=True,
        secure=settings.app.environment in {"production", "prod"},
        samesite="lax",
        path="/api/v1/auth",
    )


def _token_response(session: AuthSession, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        expires_in=settings.auth.access_token_ttl_seconds,
        user=_user_response(session.user),
    )


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный логин или пароль",
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        raise _unauthorized() from error
    _set_refresh_cookie(response, session.refresh_token, settings)
    return _token_response(session, settings)


@router.post("/refresh", response_model=TokenResponse)
@inject
async def refresh(
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> TokenResponse:
    if not refresh_token:
        raise _unauthorized()
    try:
        session = await auth.refresh(refresh_token)
    except AuthenticationError as error:
        _clear_refresh_cookie(response, settings)
        raise _unauthorized() from error
    _set_refresh_cookie(response, session.refresh_token, settings)
    return _token_response(session, settings)


@router.get("/me", response_model=UserResponse)
@inject
async def me(
    auth: FromDishka[AuthService],
    authorization: Annotated[str | None, Header()] = None,
) -> UserResponse:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    try:
        user = await auth.current_user(authorization.removeprefix("Bearer ").strip())
    except AuthenticationError as error:
        raise _unauthorized() from error
    return _user_response(user)


@router.post("/logout")
@inject
async def logout(
    response: Response,
    auth: FromDishka[AuthService],
    settings: FromDishka[Settings],
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
) -> dict[str, str]:
    await auth.logout(refresh_token)
    _clear_refresh_cookie(response, settings)
    return {"status": "ok"}
