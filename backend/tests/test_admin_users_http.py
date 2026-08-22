import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.shared.settings import load_settings

API = "/api/v1"
USERS = f"{API}/admin/users"
PASSWORD = "correct horse battery staple"


@dataclass(frozen=True, slots=True)
class Stand:
    client: AsyncClient
    admin: str
    operator: str
    prefix: str


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    prefix = f"admin-test-{uuid.uuid4().hex[:8]}"
    async with app.router.lifespan_context(app):
        passwords = PasswordService()
        async with application.database.session() as session:
            users = UserRepository(session)
            await users.create(f"{prefix}-admin", passwords.hash(PASSWORD), is_admin=True)
            await users.create(f"{prefix}-operator", passwords.hash(PASSWORD))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(client, f"{prefix}-admin", f"{prefix}-operator", prefix)
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(UserModel).where(UserModel.username.startswith(prefix)))
                    await session.commit()


async def login(client: AsyncClient, username: str, password: str = PASSWORD) -> str:
    response = await client.post(f"{API}/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_the_section_is_closed_to_anonymous_and_to_ordinary_operators(stand: Stand) -> None:
    anonymous = await stand.client.get(USERS)
    assert anonymous.status_code == 401

    operator = await login(stand.client, stand.operator)
    forbidden = await stand.client.get(USERS, headers=bearer(operator))
    assert forbidden.status_code == 403


async def test_created_employee_gets_a_password_that_is_shown_exactly_once(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    username = f"{stand.prefix}-hire"

    created = await stand.client.post(USERS, json={"username": username}, headers=bearer(token))
    assert created.status_code == 201, created.text
    password = created.json()["password"]
    assert len(password) >= 16
    assert created.headers["cache-control"] == "no-store"

    listed = await stand.client.get(USERS, headers=bearer(token))
    assert listed.status_code == 200
    hired = next(user for user in listed.json() if user["username"] == username)
    assert hired["is_active"] is True
    assert hired["is_admin"] is False
    assert password not in listed.text

    # The password works, which is the only proof it was hashed from this value.
    assert await login(stand.client, username, password)


async def test_the_same_username_cannot_be_hired_twice(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    duplicate = await stand.client.post(USERS, json={"username": stand.operator}, headers=bearer(token))
    assert duplicate.status_code == 409


async def test_an_administrator_cannot_lock_himself_out(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    me = await stand.client.get(f"{API}/auth/me", headers=bearer(token))
    admin_id = me.json()["id"]

    blocked = await stand.client.patch(f"{USERS}/{admin_id}", json={"is_active": False}, headers=bearer(token))
    assert blocked.status_code == 409
    demoted = await stand.client.patch(f"{USERS}/{admin_id}", json={"is_admin": False}, headers=bearer(token))
    assert demoted.status_code == 409


async def test_blocking_is_reversible_and_stops_the_login_in_between(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    listed = await stand.client.get(USERS, headers=bearer(token))
    operator_id = next(u["id"] for u in listed.json() if u["username"] == stand.operator)

    blocked = await stand.client.patch(f"{USERS}/{operator_id}", json={"is_active": False}, headers=bearer(token))
    assert blocked.status_code == 200
    assert blocked.json()["is_active"] is False

    refused = await stand.client.post(f"{API}/auth/login", json={"username": stand.operator, "password": PASSWORD})
    assert refused.status_code == 401

    restored = await stand.client.patch(f"{USERS}/{operator_id}", json={"is_active": True}, headers=bearer(token))
    assert restored.status_code == 200
    assert await login(stand.client, stand.operator)


async def test_revoked_admin_loses_the_section_without_waiting_for_the_token_to_expire(stand: Stand) -> None:
    """The whole reason the flag is read from the database on every request."""
    admin_token = await login(stand.client, stand.admin)
    listed = await stand.client.get(USERS, headers=bearer(admin_token))
    operator_id = next(u["id"] for u in listed.json() if u["username"] == stand.operator)

    promoted = await stand.client.patch(f"{USERS}/{operator_id}", json={"is_admin": True}, headers=bearer(admin_token))
    assert promoted.status_code == 200
    operator_token = await login(stand.client, stand.operator)
    assert (await stand.client.get(USERS, headers=bearer(operator_token))).status_code == 200

    demoted = await stand.client.patch(f"{USERS}/{operator_id}", json={"is_admin": False}, headers=bearer(admin_token))
    assert demoted.status_code == 200
    # Same token, still valid and not revoked — only the flag changed.
    assert (await stand.client.get(USERS, headers=bearer(operator_token))).status_code == 403


async def test_reset_replaces_the_password_and_the_old_one_stops_working(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    listed = await stand.client.get(USERS, headers=bearer(token))
    operator_id = next(u["id"] for u in listed.json() if u["username"] == stand.operator)

    reset = await stand.client.post(f"{USERS}/{operator_id}/password", headers=bearer(token))
    assert reset.status_code == 200, reset.text
    issued = reset.json()["password"]

    stale = await stand.client.post(f"{API}/auth/login", json={"username": stand.operator, "password": PASSWORD})
    assert stale.status_code == 401
    assert await login(stand.client, stand.operator, issued)


async def test_an_employee_changes_his_own_password_and_needs_the_current_one(stand: Stand) -> None:
    token = await login(stand.client, stand.operator)

    wrong = await stand.client.post(
        f"{API}/auth/password",
        json={"current_password": "not it", "new_password": "a brand new secret"},
        headers=bearer(token),
    )
    assert wrong.status_code == 401

    changed = await stand.client.post(
        f"{API}/auth/password",
        json={"current_password": PASSWORD, "new_password": "a brand new secret"},
        headers=bearer(token),
    )
    assert changed.status_code == 200
    assert await login(stand.client, stand.operator, "a brand new secret")


async def test_an_empty_patch_is_rejected_instead_of_reporting_success(stand: Stand) -> None:
    token = await login(stand.client, stand.admin)
    listed = await stand.client.get(USERS, headers=bearer(token))
    operator_id = next(u["id"] for u in listed.json() if u["username"] == stand.operator)

    empty = await stand.client.patch(f"{USERS}/{operator_id}", json={}, headers=bearer(token))
    assert empty.status_code == 400
