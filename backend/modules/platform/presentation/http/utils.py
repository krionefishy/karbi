from fastapi import HTTPException, Response, status

from backend.modules.platform.application import AuthSession
from backend.modules.platform.domain import User
from backend.modules.platform.presentation.http.schemas import TokenResponse, UserResponse
from backend.shared.settings import Settings

REFRESH_COOKIE = "karbi_refresh"


def user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username)


def token_response(session: AuthSession, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=session.access_token,
        expires_in=settings.auth.access_token_ttl_seconds,
        user=user_response(session.user),
    )


def set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
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


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(
        key=REFRESH_COOKIE,
        httponly=True,
        secure=settings.app.environment in {"production", "prod"},
        samesite="lax",
        path="/api/v1/auth",
    )


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверный логин или пароль",
        headers={"WWW-Authenticate": "Bearer"},
    )


def too_many_login_attempts(retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Слишком много неудачных попыток входа — попробуйте позже",
        headers={"Retry-After": str(retry_after_seconds)},
    )
