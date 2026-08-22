import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.notifications.application import BotRegistry, DispatchService, SubscriptionService
from backend.modules.notifications.domain import Audience, Bot, MessageRequest
from backend.modules.notifications.infrastructure.postgres import (
    BotModel,
    InviteLinkModel,
    NotificationRepository,
    OutgoingMessageModel,
    SubscriptionModel,
)
from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.modules.notifications.infrastructure.telegram import (
    TelegramPermanentError,
    TelegramRateLimitError,
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


class FakeRelay(RelayClient):
    """Records what would have been sent, and can be told to fail."""

    def __init__(self, error: Exception | None = None) -> None:
        super().__init__(SETTINGS.relay)
        self.sent: list[tuple[str, str]] = []
        self.keys: list[str] = []
        self.error = error

    async def send(self, *, bot_code: str, recipient: str, text: str, idempotency_key: str) -> str | None:
        if self.error is not None:
            raise self.error
        self.sent.append((recipient, text))
        self.keys.append(idempotency_key)
        return str(100 + len(self.sent))


@pytest_asyncio.fixture
async def notifications() -> AsyncIterator[tuple[Database, Bot]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    async with database.session() as session:
        repository = NotificationRepository(session)
        bot = await BotRegistry(session, repository).register(
            code=f"test-bot-{uuid.uuid4().hex[:8]}",
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

    assert invite.url == f"https://t.me/test_bot?start={invite.token}"
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
        service = DispatchService(session, repository, BotRegistry(session, repository))
        return await service.queue(message)


async def deliver(database: Database, client: RelayClient):
    async with database.session() as session:
        repository = NotificationRepository(session)
        service = DispatchService(session, repository, BotRegistry(session, repository))
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
    client = FakeRelay()

    report = await deliver(database, client)

    # The greeting from /start goes through the very same queue.
    assert report.sent == 1
    assert client.sent[0][0] == "555"
    assert (await deliver(database, client)).sent == 0


async def test_a_blocked_chat_is_not_retried_forever(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    report = await deliver(database, FakeRelay(TelegramPermanentError("bot was blocked by the user")))

    assert (report.sent, report.failed) == (0, 1)
    async with database.session() as session:
        row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert row is not None and row.status == "failed"
        # The chat is gone for good, so the subscription goes with it.
        subscription = await session.scalar(select(SubscriptionModel).where(SubscriptionModel.bot_id == bot.id))
        assert subscription is not None and subscription.is_active is False


async def test_a_telegram_outage_keeps_the_message_queued(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    report = await deliver(database, FakeRelay(TelegramTemporaryError("Bad Gateway")))

    assert (report.sent, report.retried, report.failed) == (0, 1, 0)
    async with database.session() as session:
        row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert row is not None and row.status == "queued" and row.attempts == 1


async def test_a_rate_limit_waits_out_retry_after_without_burning_attempts(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)

    report = await deliver(
        database, FakeRelay(TelegramRateLimitError("Too Many Requests: retry after 30", retry_after=30))
    )

    assert (report.sent, report.retried, report.failed) == (0, 1, 0)
    async with database.session() as session:
        row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert row is not None and row.status == "queued"
        assert row.attempts == 0
        assert row.next_attempt_at >= datetime.now(UTC) + timedelta(seconds=25)
    # Not due until Telegram's pause is over.
    assert (await deliver(database, FakeRelay())).sent == 0


async def test_one_bot_the_relay_refuses_does_not_stop_delivery_for_the_rest(notifications) -> None:
    """A dead bot costs its own queue and nothing else.

    Tokens no longer live here, so the failure arrives as a permanent refusal
    from the relay instead of an undecryptable column.
    """
    database, bot = notifications
    async with database.session() as session:
        repository = NotificationRepository(session)
        broken = await BotRegistry(session, repository).register(
            code=f"test-bot-{uuid.uuid4().hex[:8]}",
            title="Сломанный",
            invite_link_template="https://t.me/test_bot?start={token}",
        )
    try:
        async with database.session() as session:
            await NotificationRepository(session).queue_message(
                bot_id=broken.id,
                chat_id=999,
                dedupe_key=f"broken:{uuid.uuid4().hex}",
                template="subscription.confirmed",
                params={},
                text="никогда не уйдёт",
            )
            await session.commit()
        await subscribe(database, bot, chat_id=555, update_id=1)

        class PickyRelay(FakeRelay):
            async def send(self, *, bot_code: str, recipient: str, text: str, idempotency_key: str) -> str | None:
                if bot_code == broken.code:
                    raise TelegramPermanentError("bot was blocked by the user")
                return await super().send(
                    bot_code=bot_code, recipient=recipient, text=text, idempotency_key=idempotency_key
                )

        client = PickyRelay()

        report = await deliver(database, client)

        assert (report.sent, report.failed) == (1, 1)
        assert client.sent[0][0] == "555"
        async with database.session() as session:
            row = await session.scalar(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == broken.id))
            assert row is not None and row.status == "failed"
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id == broken.id))
            await session.commit()


async def test_a_crash_mid_batch_keeps_the_messages_already_sent(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    await subscribe(database, bot, chat_id=556, update_id=2)

    class CrashingRelay(FakeRelay):
        async def send(self, *, bot_code: str, recipient: str, text: str, idempotency_key: str) -> str | None:
            if self.sent:
                raise RuntimeError("the process died here")
            return await super().send(
                bot_code=bot_code, recipient=recipient, text=text, idempotency_key=idempotency_key
            )

    with pytest.raises(RuntimeError):
        await deliver(database, CrashingRelay())

    async with database.session() as session:
        rows = await session.scalars(select(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == bot.id))
        assert sorted(row.status for row in rows) == ["queued", "sent"]


async def test_racing_invite_requests_leave_exactly_one_live_link(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        repository = NotificationRepository(session)
        service = SubscriptionService(session, repository)
        first = await service.create_invite(bot, seller_id=SELLER_ID, seller_name="ООО Ромашка")

        # A concurrent request whose revoke ran before `first` was committed:
        # it sees nothing to revoke, and its insert hits the partial unique index.
        original_revoke = repository.revoke_invites
        calls = 0

        async def racy_revoke(bot_id: uuid.UUID, seller_id: uuid.UUID) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            await original_revoke(bot_id, seller_id)

        repository.revoke_invites = racy_revoke  # type: ignore[method-assign]
        second = await service.create_invite(bot, seller_id=SELLER_ID, seller_name="ООО Ромашка")

    assert calls == 2  # the conflict really happened and was retried
    assert second.token != first.token
    async with database.session() as session:
        live = list(
            await session.scalars(
                select(InviteLinkModel).where(
                    InviteLinkModel.bot_id == bot.id,
                    InviteLinkModel.seller_id == SELLER_ID,
                    InviteLinkModel.used_at.is_(None),
                    InviteLinkModel.revoked_at.is_(None),
                )
            )
        )
    assert [invite.token for invite in live] == [second.token]


async def test_group_commands_with_a_bot_suffix_are_recognized(notifications) -> None:
    database, bot = notifications
    async with database.session() as session:
        invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
            bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
        )

    await handle(database, bot, update(f"/start@any_bot_name {invite.token}", update_id=1))
    async with database.session() as session:
        assert await NotificationRepository(session).subscribers(bot.id, SELLER_ID) == [555]

    await handle(database, bot, update("/stop@any_bot_name", update_id=2))
    async with database.session() as session:
        assert await NotificationRepository(session).subscribers(bot.id, SELLER_ID) == []


async def test_the_same_producer_key_through_two_bots_reaches_the_chat_twice(notifications) -> None:
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    async with database.session() as session:
        repository = NotificationRepository(session)
        second_bot = await BotRegistry(session, repository).register(
            code=f"test-bot-{uuid.uuid4().hex[:8]}",
            title="Второй",
            invite_link_template="https://t.me/test_bot?start={token}",
        )
    try:
        async with database.session() as session:
            invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
                second_bot, seller_id=SELLER_ID, seller_name="ООО Ромашка"
            )
        await handle(database, second_bot, update(f"/start {invite.token}", update_id=1))

        first = await dispatch_queue(database, request(bot.code))
        second = await dispatch_queue(database, request(second_bot.code))

        assert (first.queued, second.queued, second.skipped) == (1, 1, 0)
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id == second_bot.id))
            await session.commit()


async def test_delivery_only_touches_the_bot_it_was_asked_for(notifications) -> None:
    """Each bot's loop drains its own queue, so a stuck bot holds up only itself."""
    database, bot = notifications
    await subscribe(database, bot, chat_id=555, update_id=1)
    async with database.session() as session:
        repository = NotificationRepository(session)
        other = await BotRegistry(session, repository).register(
            code=f"test-bot-{uuid.uuid4().hex[:8]}",
            title="Другой",
            invite_link_template="https://t.me/test_bot?start={token}",
        )
    try:
        async with database.session() as session:
            invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
                other, seller_id=SELLER_ID, seller_name="ООО Ромашка"
            )
        await handle(database, other, update(f"/start {invite.token}", update_id=1))

        await dispatch_queue(database, request(bot.code, dedupe_key="d1"))
        await dispatch_queue(database, request(other.code, dedupe_key="d2"))

        client = FakeRelay()
        async with database.session() as session:
            repository = NotificationRepository(session)
            service = DispatchService(session, repository, BotRegistry(session, repository))
            report = await service.deliver_due(client, max_attempts=3, backoff_seconds=1, bot_id=other.id)

        assert report.failed == 0
        async with database.session() as session:
            rows = list(await session.scalars(select(OutgoingMessageModel).where(OutgoingMessageModel.chat_id == 555)))
        statuses = {bot_id: {row.status for row in rows if row.bot_id == bot_id} for bot_id in (bot.id, other.id)}
        # Everything of the bot we asked for went out; nothing of the other bot moved.
        assert statuses[other.id] == {"sent"}
        assert statuses[bot.id] == {"queued"}
        assert report.sent == len([row for row in rows if row.bot_id == other.id])
    finally:
        async with database.session() as session:
            await session.execute(delete(OutgoingMessageModel).where(OutgoingMessageModel.bot_id == other.id))
            await session.execute(delete(BotModel).where(BotModel.id == other.id))
            await session.commit()
