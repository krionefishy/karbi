import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: uuid.UUID


def require_authenticated(request: Request) -> AuthenticatedPrincipal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthenticatedPrincipal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


CurrentPrincipal = Annotated[AuthenticatedPrincipal, Depends(require_authenticated)]

__all__ = ["AuthenticatedPrincipal", "CurrentPrincipal", "require_authenticated"]
