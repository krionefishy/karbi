import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.api.router import OPEN_ROUTERS, SERVICE_ROUTERS
from backend.app.factory import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.domain import SERVICE_IDS, SERVICES
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.shared.settings import load_settings

API = "/api/v1"
USERS = f"{API}/admin/users"
PASSWORD = "correct horse battery staple"
NAV = Path("frontend/src/features/automations/nav.ts")

# Роутеры, которые намеренно не закрыты доступом к сервису. Новый открытый роутер называется здесь явно.
OPEN_ROUTER_TAGS = {
    "auth",  # вход и сессия
    "admin-users",  # CurrentAdmin
    "admin-bots",  # CurrentAdmin
    "onec",  # машинный токен 1С
    "wb-sellers",  # реестр селлеров нужен любому сервису
    "wb-returns-extension",  # расширение браузера со своим токеном
    "automations",  # каталог сам фильтруется по доступу, подключение селлеров проверяет id из пути
}
ROUTER_FILE = Path("backend/app/api/router.py")


@dataclass(frozen=True, slots=True)
class Stand:
    application: Application
    client: AsyncClient
    admin: str
    operator: str
    prefix: str


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    prefix = f"access-test-{uuid.uuid4().hex[:8]}"
    async with app.router.lifespan_context(app):
        passwords = PasswordService()
        async with application.database.session() as session:
            users = UserRepository(session)
            await users.create(f"{prefix}-admin", passwords.hash(PASSWORD), is_admin=True)
            await users.create(f"{prefix}-operator", passwords.hash(PASSWORD))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(application, client, f"{prefix}-admin", f"{prefix}-operator", prefix)
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(UserModel).where(UserModel.username.startswith(prefix)))
                    await session.commit()


async def login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(f"{API}/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def operator_id(stand: Stand, admin: dict[str, str]) -> str:
    listed = await stand.client.get(USERS, headers=admin)
    return next(user["id"] for user in listed.json() if user["username"] == stand.operator)


def test_catalog_ids_match_the_frontend_navigation() -> None:
    nav_ids = set(re.findall(r'\{ id: "([a-z-]+)"', NAV.read_text()))
    assert nav_ids == SERVICE_IDS


def test_only_the_explicit_list_of_routers_is_open() -> None:
    assert {tag for open_router in OPEN_ROUTERS for tag in open_router.tags} == OPEN_ROUTER_TAGS


def test_routers_are_included_only_through_the_helpers() -> None:
    source = ROUTER_FILE.read_text()
    assert source.count("router.include_router(") == 2, "include_service и include_open — единственные входы"


def test_every_catalog_service_has_a_router_and_every_router_a_known_service() -> None:
    assert {service_id for _, service_id in SERVICE_ROUTERS} == SERVICE_IDS


@pytest.mark.parametrize("service_id", sorted(SERVICE_IDS))
async def test_an_operator_without_the_service_gets_403_and_with_it_does_not(stand: Stand, service_id: str) -> None:
    route = next(
        route
        for service_router, owner in SERVICE_ROUTERS
        if owner == service_id
        for route in service_router.routes
        if isinstance(route, APIRoute)
    )
    path = API + re.sub(r"\{[^}]+\}", str(uuid.uuid4()), route.path)
    method = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})[0]
    operator = await login(stand.client, stand.operator)

    refused = await stand.client.request(method, path, headers=operator)
    assert refused.status_code == 403
    assert refused.headers["x-access-denied"] == service_id

    admin = await login(stand.client, stand.admin)
    granted = await stand.client.put(
        f"{USERS}/{await operator_id(stand, admin)}/services", json={"service_ids": [service_id]}, headers=admin
    )
    assert granted.status_code == 200, granted.text
    allowed = await stand.client.request(method, path, headers=operator)
    assert allowed.status_code != 403


async def test_a_new_employee_sees_nothing_and_an_administrator_sees_everything(stand: Stand) -> None:
    operator = await login(stand.client, stand.operator)
    admin = await login(stand.client, stand.admin)

    assert (await stand.client.get(f"{API}/auth/me", headers=operator)).json()["services"] == []
    assert (await stand.client.get(f"{API}/automations", headers=operator)).json() == []
    everything = [service.id for service in SERVICES]
    assert (await stand.client.get(f"{API}/auth/me", headers=admin)).json()["services"] == everything
    assert {item["id"] for item in (await stand.client.get(f"{API}/automations", headers=admin)).json()} == set(
        everything
    )


async def test_granted_services_appear_in_me_and_the_catalog_and_revoking_is_immediate(stand: Stand) -> None:
    admin = await login(stand.client, stand.admin)
    operator = await login(stand.client, stand.operator)
    user_id = await operator_id(stand, admin)

    granted = await stand.client.put(
        f"{USERS}/{user_id}/services", json={"service_ids": ["wb-turnover", "fin-reports"]}, headers=admin
    )
    assert granted.status_code == 200
    assert granted.json()["services"] == ["wb-turnover", "fin-reports"]
    assert (await stand.client.get(f"{API}/auth/me", headers=operator)).json()["services"] == [
        "wb-turnover",
        "fin-reports",
    ]
    assert {item["id"] for item in (await stand.client.get(f"{API}/automations", headers=operator)).json()} == {
        "wb-turnover",
        "fin-reports",
    }
    assert (await stand.client.get(f"{API}/automations/fin-reports/sellers", headers=operator)).status_code == 200
    assert (await stand.client.get(f"{API}/automations/wb-reviews/sellers", headers=operator)).status_code == 403

    revoked = await stand.client.put(f"{USERS}/{user_id}/services", json={"service_ids": []}, headers=admin)
    assert revoked.json()["services"] == []
    assert (await stand.client.get(f"{API}/automations/fin-reports/sellers", headers=operator)).status_code == 403


async def test_the_list_carries_services_and_the_catalog_endpoint_lists_all(stand: Stand) -> None:
    admin = await login(stand.client, stand.admin)
    user_id = await operator_id(stand, admin)
    await stand.client.put(f"{USERS}/{user_id}/services", json={"service_ids": ["wb-podsort"]}, headers=admin)

    listed = (await stand.client.get(USERS, headers=admin)).json()
    assert next(user for user in listed if user["id"] == user_id)["services"] == ["wb-podsort"]
    catalog = (await stand.client.get(f"{USERS}/services", headers=admin)).json()
    assert [item["id"] for item in catalog] == [service.id for service in SERVICES]


async def test_unknown_services_and_unknown_users_are_refused(stand: Stand) -> None:
    admin = await login(stand.client, stand.admin)
    user_id = await operator_id(stand, admin)

    unknown = await stand.client.put(f"{USERS}/{user_id}/services", json={"service_ids": ["nope"]}, headers=admin)
    assert unknown.status_code == 422
    missing = await stand.client.put(f"{USERS}/{uuid.uuid4()}/services", json={"service_ids": []}, headers=admin)
    assert missing.status_code == 404


async def test_only_an_administrator_can_change_access(stand: Stand) -> None:
    admin = await login(stand.client, stand.admin)
    operator = await login(stand.client, stand.operator)
    user_id = await operator_id(stand, admin)

    refused = await stand.client.put(
        f"{USERS}/{user_id}/services", json={"service_ids": ["fin-reports"]}, headers=operator
    )
    assert refused.status_code == 403
