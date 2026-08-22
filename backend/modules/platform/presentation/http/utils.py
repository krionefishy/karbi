from fastapi import HTTPException, Response, status

from backend.modules.platform.application import AuthSession
from backend.modules.platform.domain import User
from backend.modules.platform.presentation.http.schemas import EmployeeResponse, TokenResponse, UserResponse
from backend.shared.settings import Settings

REFRESH_COOKIE = "karbi_refresh"


def user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username, is_admin=user.is_admin)


def employee_response(user: User) -> EmployeeResponse:
    return EmployeeResponse(
        id=str(user.id),
        username=user.username,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


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


def username_taken(username: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Сотрудник {username} уже заведён")


def employee_not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")


def nothing_to_update() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нечего менять")


def self_lockout() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Нельзя снять права или заблокировать самого себя",
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
