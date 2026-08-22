import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from backend.modules.platform.application import AuthenticationError, AuthService


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    user_id: uuid.UUID
    username: str


def require_authenticated(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthenticatedPrincipal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def require_admin(request: Request) -> AdminPrincipal:
    """Read the admin flag from the database on every request.

    Carrying it in the access token would be one query cheaper and a day late:
    the token lives 24 hours, so a revoked administrator would keep the section
    until it expired.
    """
    principal = require_authenticated(request)
    auth = await request.state.dishka_container.get(AuthService)
    try:
        user = await auth.current_user(principal.user_id)
    except AuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return AdminPrincipal(user_id=user.id, username=user.username)


CurrentPrincipal = Annotated[AuthenticatedPrincipal, Depends(require_authenticated)]
CurrentAdmin = Annotated[AdminPrincipal, Depends(require_admin)]

__all__ = [
    "AdminPrincipal",
    "AuthenticatedPrincipal",
    "CurrentAdmin",
    "CurrentPrincipal",
    "require_admin",
    "require_authenticated",
]
