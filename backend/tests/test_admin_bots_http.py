import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, select

from backend.app.application import Application
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.platform.application import PasswordService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.platform.infrastructure.postgres.models import UserModel
from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
BOTS = "/api/v1/admin/bots"
RELAY_BOTS = f"{SETTINGS.relay.base_url}/api/v1/bots"
PASSWORD = "correct horse battery staple"
TOKEN = "1234:SUPER-SECRET-BOT-TOKEN"

RELAY_OK = {
    "bot_code": "turnover-alerts",
    "title": "Оборачиваемость",
    "invite_link_template": "https://t.me/mplace_auto_bot?start={token}",
}


@dataclass(frozen=True, slots=True)
class Stand:
    client: AsyncClient
    application: Application
    prefix: str
    code: str


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    application = Application(SETTINGS)
    app = application.get_app()
    prefix = f"bots-test-{uuid.uuid4().hex[:8]}"
    async with app.router.lifespan_context(app):
        passwords = PasswordService()
        async with application.database.session() as session:
            users = UserRepository(session)
            await users.create(f"{prefix}-admin", passwords.hash(PASSWORD), is_admin=True)
            await users.create(f"{prefix}-operator", passwords.hash(PASSWORD))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(client, application, prefix, f"{prefix}-bot")
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(BotModel).where(BotModel.code.startswith(prefix)))
                    await session.execute(delete(UserModel).where(UserModel.username.startswith(prefix)))
                    await session.commit()


async def sign_in(stand: Stand, admin: bool = True) -> None:
    suffix = "admin" if admin else "operator"
    response = await stand.client.post(
        "/api/v1/auth/login", json={"username": f"{stand.prefix}-{suffix}", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    stand.client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


async def register(stand: Stand, relay_response: Response, code: str | None = None) -> Response:
    with respx.mock(assert_all_called=False) as router:
        router.post(RELAY_BOTS).mock(return_value=relay_response)
        return await stand.client.post(
            BOTS, json={"code": code or stand.code, "title": "Оборачиваемость", "token": TOKEN}
        )


class TestAccess:
    async def test_an_anonymous_caller_sees_nothing(self, stand: Stand) -> None:
        assert (await stand.client.get(BOTS)).status_code == 401

    async def test_an_operator_is_not_allowed_in(self, stand: Stand) -> None:
        await sign_in(stand, admin=False)
        assert (await stand.client.get(BOTS)).status_code == 403


class TestRegistration:
    async def test_a_registered_bot_appears_in_the_list(self, stand: Stand) -> None:
        await sign_in(stand)

        created = await register(stand, Response(200, json=RELAY_OK))

        assert created.status_code == 201, created.text
        assert created.json()["invite_link_template"] == "https://t.me/mplace_auto_bot?start={token}"
        listed = await stand.client.get(BOTS)
        assert stand.code in [bot["code"] for bot in listed.json()]

    async def test_the_token_is_sent_to_the_relay_and_stored_nowhere_here(self, stand: Stand) -> None:
        await sign_in(stand)

        with respx.mock(assert_all_called=False) as router:
            route = router.post(RELAY_BOTS).mock(return_value=Response(200, json=RELAY_OK))
            await stand.client.post(BOTS, json={"code": stand.code, "title": "T", "token": TOKEN})

        assert TOKEN in route.calls.last.request.content.decode()
        async with stand.application.database.session() as session:
            row = await session.scalar(select(BotModel).where(BotModel.code == stand.code))
        assert row is not None
        # Nothing on this row may carry the token, in any column.
        assert TOKEN not in "".join(str(value) for value in row.__dict__.values())

    async def test_a_rejected_token_leaves_no_bot_behind(self, stand: Stand) -> None:
        await sign_in(stand)

        response = await register(stand, Response(422, json={"detail": "Unauthorized"}))

        assert response.status_code == 422
        async with stand.application.database.session() as session:
            assert await session.scalar(select(BotModel).where(BotModel.code == stand.code)) is None

    async def test_an_unreachable_relay_is_not_reported_as_a_bad_token(self, stand: Stand) -> None:
        await sign_in(stand)

        response = await register(stand, Response(502, json={"detail": "relay down"}))

        # Saying "bad token" here would send the operator hunting for a problem
        # that is not theirs.
        assert response.status_code == 503

    async def test_a_validation_error_never_echoes_the_token(self, stand: Stand) -> None:
        await sign_in(stand)

        response = await stand.client.post(BOTS, json={"code": "BAD CODE", "title": "T", "token": TOKEN})

        assert response.status_code == 422
        assert TOKEN not in response.text


class TestDeletion:
    async def test_deleting_removes_it_from_both_sides(self, stand: Stand) -> None:
        await sign_in(stand)
        await register(stand, Response(200, json=RELAY_OK))

        with respx.mock(assert_all_called=False) as router:
            route = router.post(RELAY_BOTS).mock(return_value=Response(200, json={"bot_code": stand.code}))
            deleted = await stand.client.delete(f"{BOTS}/{stand.code}")

        assert deleted.status_code == 204
        # The relay is told too: a token must not outlive the bot it belongs to.
        assert '"action":"delete"' in route.calls.last.request.content.decode().replace(" ", "")
        listed = await stand.client.get(BOTS)
        assert stand.code not in [bot["code"] for bot in listed.json()]

    async def test_deleting_an_unknown_bot_is_a_404(self, stand: Stand) -> None:
        await sign_in(stand)
        assert (await stand.client.delete(f"{BOTS}/nobody-registered-this")).status_code == 404
