import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from backend.modules.platform.application import AuthenticationError, AuthService, ServiceAccessService
from backend.modules.platform.domain import SERVICE_IDS


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


SERVICE_DENIED_HEADER = "X-Access-Denied"


def require_service(service_id: str) -> Callable[[Request], Awaitable[AuthenticatedPrincipal]]:
    """Пустить к сервису только того, кому админ его выдал.

    Читает базу на каждый запрос, как `require_admin`: права не едут в токене, и снятие
    доступа действует сразу. Заодно это первое место на рабочих маршрутах, где
    блокировка сотрудника срабатывает немедленно, а не к концу суток.
    """

    async def dependency(request: Request) -> AuthenticatedPrincipal:
        principal = require_authenticated(request)
        container = request.state.dishka_container
        auth = await container.get(AuthService)
        try:
            user = await auth.current_user(principal.user_id)
        except AuthenticationError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Требуется авторизация",
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        if not await (await container.get(ServiceAccessService)).allows(user, service_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Нет доступа к этому сервису",
                headers={SERVICE_DENIED_HEADER: service_id},
            )
        return principal

    dependency.service_id = service_id  # type: ignore[attr-defined]
    return dependency


async def require_path_service(request: Request) -> AuthenticatedPrincipal:
    """Доступ к автоматизации из `{automation_id}` в пути; её id совпадает с id сервиса.

    Неизвестный id пропускаем дальше: 404 ему вернёт сам обработчик, а не проверка доступа.
    """
    service_id = str(request.path_params.get("automation_id", ""))
    if service_id not in SERVICE_IDS:
        return require_authenticated(request)
    return await require_service(service_id)(request)


CurrentPrincipal = Annotated[AuthenticatedPrincipal, Depends(require_authenticated)]
CurrentAdmin = Annotated[AdminPrincipal, Depends(require_admin)]
AutomationPrincipal = Annotated[AuthenticatedPrincipal, Depends(require_path_service)]

__all__ = [
    "SERVICE_DENIED_HEADER",
    "AdminPrincipal",
    "AutomationPrincipal",
    "AuthenticatedPrincipal",
    "CurrentAdmin",
    "CurrentPrincipal",
    "require_admin",
    "require_authenticated",
    "require_service",
]
