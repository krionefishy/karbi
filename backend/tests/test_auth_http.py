import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.modules.platform.presentation.http.utils import REFRESH_COOKIE
from backend.shared.settings import load_settings

API = "/api/v1"
PASSWORD = "correct horse battery staple"


@pytest_asyncio.fixture
async def auth_stand() -> AsyncIterator[tuple[AsyncClient, str]]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    username = f"auth-user-{uuid.uuid4().hex[:12]}"
    async with app.router.lifespan_context(app):
        async with application.database.session() as session:
            await UserRepository(session).create(username, PasswordService().hash(PASSWORD))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield client, username
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(UserModel).where(UserModel.username == username))
                    await session.commit()


async def login(client: AsyncClient, username: str, password: str = PASSWORD):
    return await client.post(f"{API}/auth/login", json={"username": username, "password": password})


async def test_refresh_rotates_the_cookie_and_rejects_the_old_one(auth_stand) -> None:
    client, username = auth_stand

    logged_in = await login(client, username)
    assert logged_in.status_code == 200, logged_in.text
    first = logged_in.cookies[REFRESH_COOKIE]

    def present(token: str) -> None:
        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE, token)

    present(first)
    refreshed = await client.post(f"{API}/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    second = refreshed.cookies[REFRESH_COOKIE]
    assert second != first

    present(first)
    replayed = await client.post(f"{API}/auth/refresh")
    assert replayed.status_code == 401

    present(second)
    rotated_again = await client.post(f"{API}/auth/refresh")
    assert rotated_again.status_code == 200


async def test_logout_invalidates_the_access_token(auth_stand) -> None:
    client, username = auth_stand

    logged_in = await login(client, username)
    headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 200

    logged_out = await client.post(f"{API}/auth/logout", headers=headers)
    assert logged_out.status_code == 200

    assert (await client.get(f"{API}/auth/me", headers=headers)).status_code == 401


async def test_failed_logins_lock_only_that_login(auth_stand) -> None:
    client, username = auth_stand

    for _ in range(10):
        assert (await login(client, username, "wrong password")).status_code == 401

    locked_out = await login(client, username)
    assert locked_out.status_code == 429
    assert "Retry-After" in locked_out.headers

    other = await login(client, f"other-{uuid.uuid4().hex[:12]}", "wrong password")
    assert other.status_code == 401
