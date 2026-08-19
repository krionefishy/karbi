import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.notifications.application import BotRegistry, DispatchService, SubscriptionService
from backend.modules.notifications.domain import Audience, Bot, MessageRequest
from backend.modules.notifications.infrastructure.postgres import (
    BotModel,
    NotificationRepository,
    OutgoingMessageModel,
    SubscriptionModel,
)
from backend.modules.notifications.infrastructure.telegram import (
    TelegramClient,
    TelegramPermanentError,
    TelegramTemporaryError,
    Update,
)
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SELLER_ID = uuid.uuid4()


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


class FakeTelegram(TelegramClient):
    """Records what would have been sent, and can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(base_url="http://telegram.invalid")
        self.sent: list[tuple[int, str]] = []
        self.error = error

    async def send(self, token: str, chat_id: int, text: str) -> int | None:
        if self.error is not None:
            raise self.error
        self.sent.append((chat_id, text))
        return 100 + len(self.sent)


@pytest_asyncio.fixture
async def notifications() -> AsyncIterator[tuple[Database, Bot]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    async with database.session() as session:
        repository = NotificationRepository(session)
        bot = await BotRegistry(session, repository, cipher()).register(
            code=f"test-bot-{uuid.uuid4().hex[:8]}",
            username="karbi_test_bot",
            title="Оборачиваемость",
            token=f"1234:{uuid.uuid4().hex}",
        )
    try:
        yield database, bot
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id == bot.id))
            await session.commit()
        await database.disconnect()


def update(text: str, chat_id: int = 555, update_id: int = 1) -> Update:
    return Update(
        update_id=update_id,
        chat_id=chat_id,
        text=text,
        user_id=777,
        username="buyer",
        first_name="Иван",
    )


async def handle(database: Database, bot: Bot, message: Update) -> None:
    async with database.session() as session:
        repository = NotificationRepository(session)
        await SubscriptionService(session, repository).handle_update(bot, message)
        await repository.save_cursor(bot.id, message.update_id)
        await session.commit()


async def queued_texts(database: Database, bot: Bot) -> list[str]:
    async with database.session() as session:
        rows = await session.scalars(
            select(OutgoingMessageModel)
            .where(OutgoingMessageModel.bot_id == bot.id)
            .order_by(OutgoingMessageModel.created_at)
        )
        return [row.text for row in rows]


async def test_the_invite_link_carries_only_a_random_token(notifications) -> None:
    database, bot = notifications

    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )

    assert invite.url == f"https://t.me/{bot.username}?start={invite.token}"
    assert str(SELLER_ID) not in invite.url
    assert len(invite.token) <= 64


async def test_start_binds_the_chat_to_the_seller_and_greets_by_name(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )

    await handle(database, bot, update(f"/start {invite.token}"))

    async with database.session() as session:
        subscribers = await NotificationRepository(session).subscribers(bot.id, SELLER_ID)
    assert subscribers == [555]
    greeting = (await queued_texts(database, bot))[0]
    assert "ООО Ромашка" in greeting
    assert "Оборачиваемость" in greeting


async def test_a_spent_link_cannot_subscribe_a_second_chat(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )
    await handle(database, bot, update(f"/start {invite.token}", chat_id=555, update_id=1))

    await handle(database, bot, update(f"/start {invite.token}", chat_id=556, update_id=2))

    async with database.session() as session:
        subscribers = await NotificationRepository(session).subscribers(bot.id, SELLER_ID)
    assert subscribers == [555]
    assert "Ссылка не подошла" in (await queued_texts(database, bot))[1]


async def test_issuing_a_new_link_voids_the_previous_one(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        service = SubscriptionService(session, NotificationRepository(session))
        first = await service.create_invite(bot, seller_id=SELLER_ID, seller_name="ООО Ромашка")
        await service.create_invite(bot, seller_id=SELLER_ID, seller_name="ООО Ромашка")

    await handle(database, bot, update(f"/start {first.token}"))

    async with database.session() as session:
        assert await NotificationRepository(session).subscribers(bot.id, SELLER_ID) == []


async def test_stop_unsubscribes_the_chat(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )
    await handle(database, bot, update(f"/start {invite.token}", update_id=1))

    await handle(database, bot, update("/stop", update_id=2))

    async with database.session() as session:
        assert await NotificationRepository(session).subscribers(bot.id, SELLER_ID) == []
        row = await session.scalar(select(SubscriptionModel).where(SubscriptionModel.bot_id == bot.id))
        # The row stays: who was subscribed and when is worth keeping.
        assert row is not None and row.is_active is False


async def test_a_message_without_a_link_explains_what_to_do(notifications) -> None:
    database, bot = notifications

    await handle(database, bot, update("привет"))

    assert "персональную ссылку" in (await queued_texts(database, bot))[0]


async def subscribe(database: Database, bot: Bot, chat_id: int, update_id: int) -> None:
    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )
    await handle(database, bot, update(f"/start {invite.token}", chat_id=chat_id, update_id=update_id))


def request(bot_code: str, dedupe_key: str = "turnover:2026-08-19", template: str = "subscription.confirmed"):
    return MessageRequest(
        message_id=str(uuid.uuid4()),
        bot_code=bot_code,
        audience=Audience("seller_subscribers", seller_id=SELLER_ID),
        template=template,
        dedupe_key=dedupe_key,
        params={"seller_name": "ООО Ромашка", "bot_title": "Оборачиваемость"},
    )


async def dispatch_queue(database: Database, message):
    async with database.session() as session:
        repository = NotificationRepository(session)
        service = DispatchService(session, repository, BotRegistry(session, repository, cipher()))
        return await service.queue(message)


async def deliver(database: Database, client: TelegramClient):
    async with database.session() as session:
        repository = NotificationRepository(session)
        service = DispatchService(session, repository, BotRegistry(session, repository, cipher()))
        return await service.deliver_due(client, max_attempts=3, backoff_seconds=1)


async def test_one_event_reaches_every_subscribed_chat(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    await subscribe(database, bot, chat_id=556, update_id=2)

    result = await dispatch_queue(database, request(bot.code))

    assert (result.queued, result.skipped) == (2, 0)


async def test_a_replayed_event_does_not_send_twice(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    message = request(bot.code)

    first = await dispatch_queue(database, message)
    second = await dispatch_queue(database, message)

    assert (first.queued, second.queued) == (1, 0)
    assert second.skipped == 1


async def test_an_unknown_bot_or_template_is_rejected_not_retried(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    unknown_bot = await dispatch_queue(database, request("does-not-exist"))
    unknown_template = await dispatch_queue(database, request(bot.code, template="turnover.nope"))

    assert unknown_bot.rejected is not None and unknown_bot.queued == 0
    assert unknown_template.rejected is not None and unknown_template.queued == 0


async def test_delivery_sends_what_was_queued(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    client = FakeTelegram()

    report = await deliver(database, client)

    # The greeting from /start goes through the very same queue.
    assert report.sent == 1
    assert client.sent[0][0] == 555
    assert (await deliver(database, client)).sent == 0


async def test_a_blocked_chat_is_not_retried_forever(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    report = await deliver(database, FakeTelegram(TelegramPermanentError("bot was blocked by the user")))

    assert (report.sent, report.failed) == (0, 1)
    async with database.session() as session:
        row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert row is not None and row.status == "failed"


async def test_a_telegram_outage_keeps_the_message_queued(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    report = await deliver(database, FakeTelegram(TelegramTemporaryError("Bad Gateway")))

    assert (report.sent, report.retried, report.failed) == (0, 1, 0)
    async with database.session() as session:
        row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert row is not None and row.status == "queued" and row.attempts == 1
