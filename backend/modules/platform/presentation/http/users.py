import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Response, status

from backend.app.http.principals import CurrentAdmin
from backend.modules.platform.application import (
    SelfLockoutError,
    ServiceAccessService,
    UnknownServiceError,
    UserAdminService,
    UsernameTakenError,
    UserNotFoundError,
)
from backend.modules.platform.presentation.http.schemas import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeUpdate,
    IssuedPasswordResponse,
    ServiceAccessUpdate,
    ServiceResponse,
)
from backend.modules.platform.presentation.http.utils import (
    employee_not_found,
    employee_response,
    nothing_to_update,
    self_lockout,
    unknown_services,
    username_taken,
)

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("", response_model=list[EmployeeResponse])
@inject
async def list_employees(
    _: CurrentAdmin, service: FromDishka[UserAdminService], access: FromDishka[ServiceAccessService]
) -> list[EmployeeResponse]:
    users = await service.list_users()
    granted = await access.granted_by_user(users)
    return [employee_response(user, granted[user.id]) for user in users]


@router.get("/services", response_model=list[ServiceResponse])
@inject
async def list_services(_: CurrentAdmin, access: FromDishka[ServiceAccessService]) -> list[ServiceResponse]:
    return [ServiceResponse(id=item.id, title=item.title) for item in access.catalog()]


@router.post("", response_model=IssuedPasswordResponse, status_code=status.HTTP_201_CREATED)
@inject
async def create_employee(
    payload: EmployeeCreate,
    response: Response,
    _: CurrentAdmin,
    service: FromDishka[UserAdminService],
    access: FromDishka[ServiceAccessService],
) -> IssuedPasswordResponse:
    try:
        user, password = await service.create(payload.username, payload.is_admin)
    except UsernameTakenError as error:
        raise username_taken(payload.username.strip()) from error
    response.headers["Cache-Control"] = "no-store"
    return IssuedPasswordResponse(user=employee_response(user, await access.granted(user)), password=password)


@router.patch("/{user_id}", response_model=EmployeeResponse)
@inject
async def update_employee(
    user_id: uuid.UUID,
    payload: EmployeeUpdate,
    admin: CurrentAdmin,
    service: FromDishka[UserAdminService],
    access: FromDishka[ServiceAccessService],
) -> EmployeeResponse:
    if payload.is_admin is None and payload.is_active is None:
        raise nothing_to_update()
    user = None
    try:
        if payload.is_admin is not None:
            user = await service.set_admin(admin.user_id, user_id, payload.is_admin)
        if payload.is_active is not None:
            user = await service.set_active(admin.user_id, user_id, payload.is_active)
    except SelfLockoutError as error:
        raise self_lockout() from error
    except UserNotFoundError as error:
        raise employee_not_found() from error
    if user is None:
        raise employee_not_found()
    return employee_response(user, await access.granted(user))


@router.put("/{user_id}/services", response_model=EmployeeResponse)
@inject
async def set_services(
    user_id: uuid.UUID,
    payload: ServiceAccessUpdate,
    admin: CurrentAdmin,
    access: FromDishka[ServiceAccessService],
) -> EmployeeResponse:
    """Заменить набор сервисов сотрудника целиком."""
    try:
        user = await access.replace(admin.user_id, user_id, payload.service_ids)
    except UnknownServiceError as error:
        raise unknown_services(error.service_ids) from error
    except UserNotFoundError as error:
        raise employee_not_found() from error
    return employee_response(user, await access.granted(user))


@router.post("/{user_id}/password", response_model=IssuedPasswordResponse)
@inject
async def reset_password(
    user_id: uuid.UUID,
    response: Response,
    _: CurrentAdmin,
    service: FromDishka[UserAdminService],
    access: FromDishka[ServiceAccessService],
) -> IssuedPasswordResponse:
    try:
        user, password = await service.reset_password(user_id)
    except UserNotFoundError as error:
        raise employee_not_found() from error
    response.headers["Cache-Control"] = "no-store"
    return IssuedPasswordResponse(user=employee_response(user, await access.granted(user)), password=password)
