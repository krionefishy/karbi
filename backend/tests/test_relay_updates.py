import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.notifications.application import BotRegistry, SubscriptionService
from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import (
    BotModel,
    NotificationRepository,
    OutgoingMessageModel,
    SubscriptionModel,
)
from backend.modules.notifications.infrastructure.relay import RelayUpdate
from backend.modules.notifications.infrastructure.telegram import TelegramTemporaryError
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.notifications.updates import UpdateFetcher

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


class FakeRelay:
    """Hands out prepared batches and records what cursor was asked for."""

    def __init__(self, batches: list[list[RelayUpdate]] | None = None, error: Exception | None = None) -> None:
        self.batches = batches or []
        self.error = error
        self.asked: list[int] = []

    async def updates(self, *, bot_code: str, after: int, wait: int, limit: int = 50) -> list[RelayUpdate]:
        self.asked.append(after)
        if self.error is not None:
            raise self.error
        return self.batches.pop(0) if self.batches else []


def update(event_id: int = 1, text: str = "/start", recipient: str = "555") -> RelayUpdate:
    return RelayUpdate(
        event_id=event_id,
        recipient=recipient,
        text=text,
        external_id="777",
        username="buyer",
        display_name="Иван",
    )


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[tuple[Database, Bot]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    async with database.session() as session:
        bot = await BotRegistry(session, NotificationRepository(session)).register(
            code=f"relay-test-{uuid.uuid4().hex[:8]}",
            title="Оборачиваемость",
            invite_link_template="https://t.me/test_bot?start={token}",
        )
    try:
        yield database, bot
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id == bot.id))
            await session.commit()
        await database.disconnect()


def fetcher(database: Database, bot: Bot, relay: FakeRelay) -> UpdateFetcher:
    return UpdateFetcher(database, relay, bot, invite_ttl_hours=72, wait_seconds=0)  # type: ignore[arg-type]


async def cursor_of(database: Database, bot: Bot) -> int:
    async with database.session() as session:
        return await NotificationRepository(session).cursor(bot.id)


class TestFetching:
    async def test_the_cursor_from_postgres_is_what_the_relay_is_asked_for(self, stand) -> None:
        database, bot = stand
        async with database.session() as session:
            await NotificationRepository(session).save_cursor(bot.id, 900)
            await session.commit()
        relay = FakeRelay()

        await fetcher(database, bot, relay).fetch_once()

        # No acknowledgement protocol: our own cursor is the whole conversation.
        assert relay.asked == [900]

    async def test_a_handled_update_moves_the_cursor(self, stand) -> None:
        database, bot = stand
        relay = FakeRelay([[update(event_id=42)]])

        assert await fetcher(database, bot, relay).fetch_once() == 1
        assert await cursor_of(database, bot) == 42

    async def test_a_repeated_update_is_not_handled_twice(self, stand) -> None:
        database, bot = stand
        # The relay repeats whenever our fetch is lost mid-flight.
        relay = FakeRelay([[update(event_id=7)], [update(event_id=7)]])
        loop = fetcher(database, bot, relay)

        assert await loop.fetch_once() == 1
        assert await loop.fetch_once() == 0

        async with database.session() as session:
            queued = list(
                await session.scalars(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
            )
        assert len(queued) == 1

    async def test_a_recipient_this_side_cannot_store_does_not_wedge_the_loop(self, stand) -> None:
        database, bot = stand
        relay = FakeRelay([[update(event_id=5, recipient="not-a-number")]])

        assert await fetcher(database, bot, relay).fetch_once() == 0
        # The cursor steps over it: otherwise the same update would be fetched
        # forever and nothing behind it would ever be seen.
        assert await cursor_of(database, bot) == 5

    async def test_an_unreachable_relay_leaves_the_cursor_alone(self, stand) -> None:
        database, bot = stand
        relay = FakeRelay(error=TelegramTemporaryError("relay unreachable"))

        with pytest.raises(TelegramTemporaryError):
            await fetcher(database, bot, relay).fetch_once()

        assert await cursor_of(database, bot) == 0

    async def test_a_start_with_a_live_invite_subscribes_the_chat(self, stand) -> None:
        database, bot = stand
        seller_id = uuid.uuid4()
        async with database.session() as session:
            repository = NotificationRepository(session)
            invite = await SubscriptionService(session, repository).create_invite(
                bot, seller_id=seller_id, seller_name="ООО Ромашка"
            )
        relay = FakeRelay([[update(event_id=3, text=f"/start {invite.token}")]])

        assert await fetcher(database, bot, relay).fetch_once() == 1

        async with database.session() as session:
            subscription = await session.scalar(select(SubscriptionModel).where(SubscriptionModel.bot_id == bot.id))
        assert subscription is not None
        assert subscription.chat_id == 555
        assert subscription.seller_id == seller_id
        assert subscription.is_active
