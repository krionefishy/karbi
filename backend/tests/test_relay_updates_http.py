import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.application import Application
from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import (
    BotModel,
    NotificationRepository,
    OutgoingMessageModel,
    SubscriptionModel,
)
from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
UPDATES = "/api/v1/internal/telegram/updates"


@dataclass(frozen=True, slots=True)
class Stand:
    client: AsyncClient
    bot_code: str
    bot_id: uuid.UUID
    application: Application


def token(
    *,
    audience: str | None = None,
    issuer: str | None = None,
    expired: bool = False,
    secret: str | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "relay",
            "jti": str(uuid.uuid4()),
            "iat": now - timedelta(seconds=600 if expired else 0),
            "exp": now - timedelta(seconds=300) if expired else now + timedelta(seconds=300),
            "iss": issuer or SETTINGS.relay.issuer,
            "aud": audience or SETTINGS.relay.inbound_audience,
        },
        secret or SETTINGS.relay.jwt_secret,
        algorithm="HS256",
    )


def headers(**kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(**kwargs)}"}


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    application = Application(SETTINGS)
    app = application.get_app()
    async with app.router.lifespan_context(app):
        code = f"relay-test-{uuid.uuid4().hex[:8]}"
        async with application.database.session() as session:
            bot = await BotRegistry(session, NotificationRepository(session)).register(
                code=code,
                title="Оборачиваемость",
                invite_link_template="https://t.me/test_bot?start={token}",
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(client, code, bot.id, application)
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(BotModel).where(BotModel.id == bot.id))
                    await session.commit()


def payload(stand: Stand, event_id: int = 1, text: str = "/start", recipient: str = "555") -> dict:
    return {
        "bot_code": stand.bot_code,
        "event_id": event_id,
        "recipient": recipient,
        "text": text,
        "sender": {"external_id": "777", "username": "buyer", "display_name": "Иван"},
    }


class TestChannelAuth:
    async def test_an_update_without_a_token_is_refused(self, stand: Stand) -> None:
        assert (await stand.client.post(UPDATES, json=payload(stand))).status_code == 401

    async def test_a_token_minted_for_the_relay_does_not_open_this_door(self, stand: Stand) -> None:
        # Same secret, other direction: the audience is the whole defence.
        response = await stand.client.post(UPDATES, json=payload(stand), headers=headers(audience="relay"))
        assert response.status_code == 401

    async def test_an_expired_token_is_refused(self, stand: Stand) -> None:
        response = await stand.client.post(UPDATES, json=payload(stand), headers=headers(expired=True))
        assert response.status_code == 401

    async def test_a_token_signed_with_another_secret_is_refused(self, stand: Stand) -> None:
        response = await stand.client.post(
            UPDATES, json=payload(stand), headers=headers(secret="a-different-secret-of-sufficient-length")
        )
        assert response.status_code == 401

    async def test_a_foreign_issuer_is_refused(self, stand: Stand) -> None:
        response = await stand.client.post(UPDATES, json=payload(stand), headers=headers(issuer="somebody-else"))
        assert response.status_code == 401


class TestAcceptance:
    async def test_an_unknown_command_is_answered_through_the_queue(self, stand: Stand) -> None:
        response = await stand.client.post(UPDATES, json=payload(stand, text="/start"), headers=headers())

        assert response.status_code == 200
        assert response.json() == {"accepted": True}
        async with stand.application.database.session() as session:
            queued = await session.scalars(
                select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == stand.bot_id)
            )
            # A reply is an ordinary outgoing message: it leaves through the relay
            # like everything else, not in the answer to this request.
            assert [row.status for row in queued] == ["queued"]

    async def test_a_replayed_update_is_not_handled_twice(self, stand: Stand) -> None:
        first = await stand.client.post(UPDATES, json=payload(stand, event_id=7), headers=headers())
        second = await stand.client.post(UPDATES, json=payload(stand, event_id=7), headers=headers())

        assert first.json() == {"accepted": True}
        # The relay replays whenever our answer is lost; the cursor is what stops
        # a second subscription from being created.
        assert second.status_code == 200
        assert second.json() == {"accepted": False}
        async with stand.application.database.session() as session:
            rows = await session.scalars(
                select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == stand.bot_id)
            )
            assert len(list(rows)) == 1

    async def test_an_older_update_is_ignored(self, stand: Stand) -> None:
        await stand.client.post(UPDATES, json=payload(stand, event_id=10), headers=headers())
        late = await stand.client.post(UPDATES, json=payload(stand, event_id=4), headers=headers())

        assert late.json() == {"accepted": False}

    async def test_the_cursor_moves_with_the_update(self, stand: Stand) -> None:
        await stand.client.post(UPDATES, json=payload(stand, event_id=42), headers=headers())

        async with stand.application.database.session() as session:
            assert await NotificationRepository(session).cursor(stand.bot_id) == 42

    async def test_an_update_for_an_unknown_bot_is_refused(self, stand: Stand) -> None:
        body = payload(stand)
        body["bot_code"] = "nobody-registered-this"
        response = await stand.client.post(UPDATES, json=body, headers=headers())
        assert response.status_code == 404

    @pytest.mark.parametrize("recipient", ["not-a-number", "12ab"])
    async def test_a_recipient_this_side_cannot_store_is_refused(self, stand: Stand, recipient: str) -> None:
        response = await stand.client.post(UPDATES, json=payload(stand, recipient=recipient), headers=headers())
        assert response.status_code == 422

    async def test_a_start_with_a_live_invite_subscribes_the_chat(self, stand: Stand) -> None:
        from backend.modules.notifications.application import SubscriptionService

        seller_id = uuid.uuid4()
        async with stand.application.database.session() as session:
            repository = NotificationRepository(session)
            bots = BotRegistry(session, repository)
            invite = await SubscriptionService(session, repository).create_invite(
                await bots.by_code(stand.bot_code), seller_id=seller_id, seller_name="ООО Ромашка"
            )

        response = await stand.client.post(
            UPDATES, json=payload(stand, event_id=3, text=f"/start {invite.token}"), headers=headers()
        )

        assert response.json() == {"accepted": True}
        async with stand.application.database.session() as session:
            subscription = await session.scalar(
                select(SubscriptionModel).where(SubscriptionModel.bot_id == stand.bot_id)
            )
            assert subscription is not None
            assert subscription.chat_id == 555
            assert subscription.seller_id == seller_id
            assert subscription.is_active
