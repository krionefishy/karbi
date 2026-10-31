"""Интерактивные боты в модуле уведомлений: сырые апдейты в топик, очередь с кнопками, правкой и ответом.

Рядом — регрессия старого пути: обычный бот при включённом флаге ведёт себя ровно как раньше.
"""

import dataclasses
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.notifications.application import BotRegistry, DispatchService, SubscriptionService
from backend.modules.notifications.domain import (
    Attachment,
    Bot,
    Callback,
    MessageRequest,
    MessengerPermanentError,
    Update,
    UpdateEvent,
)
from backend.modules.notifications.infrastructure.postgres import (
    BotModel,
    NotificationRepository,
    OutgoingMessageModel,
    SubscriptionModel,
)
from backend.modules.notifications.infrastructure.relay import RelayClient, RelayUpdate
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.notifications.updates import UpdateFetcher

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
BUTTONS = [[{"label": "Положительный", "action": "sc:o:AAAAAAAAAAAAAAAAAAAAAA:p"}]]


async def _bot(database: Database, *, interactive: bool) -> Bot:
    async with database.session() as session:
        return await BotRegistry(session, NotificationRepository(session)).register(
            code=f"ia-{uuid.uuid4().hex[:8]}",
            title="Скриншоты",
            invite_link_template="https://t.me/test_bot?start={token}",
            interactive=interactive,
        )


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[tuple[Database, Bot, Bot]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    interactive = await _bot(database, interactive=True)
    plain = await _bot(database, interactive=False)
    try:
        yield database, interactive, plain
    finally:
        async with database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.id.in_([interactive.id, plain.id])))
            await session.commit()
        await database.disconnect()


def photo_update(update_id: int = 10, text: str = "") -> Update:
    return Update(
        update_id=update_id,
        chat_id=555,
        text=text,
        user_id=777,
        username="ivan",
        first_name="Иван",
        chat_type="private",
        message_ref="4521",
        caption="полож",
        attachments=(Attachment(kind="photo", file_ref="ref1", size=1000, width=1280, height=720),),
    )


async def accept(database: Database, bot: Bot, update: Update, *, interactive_enabled: bool) -> bool:
    async with database.session() as session:
        service = SubscriptionService(session, NotificationRepository(session), interactive_enabled=interactive_enabled)
        return await service.accept_update(bot, update)


async def outbox(database: Database, bot: Bot, topic: str) -> list[dict]:
    async with database.session() as session:
        rows = await session.scalars(
            select(OutboxEventModel)
            .where(OutboxEventModel.aggregate_id == bot.id, OutboxEventModel.topic == topic)
            .order_by(OutboxEventModel.created_at)
        )
        return [row.payload for row in rows]


async def queued(database: Database, bot: Bot) -> list[OutgoingMessageModel]:
    async with database.session() as session:
        return list(
            await session.scalars(
                select(OutgoingMessageModel)
                .where(OutgoingMessageModel.bot_id == bot.id)
                .order_by(OutgoingMessageModel.created_at)
            )
        )


class TestInboundInteractive:
    async def test_a_photo_goes_to_the_topic_whole_and_nothing_is_answered(self, stand) -> None:
        database, bot, _ = stand

        assert await accept(database, bot, photo_update(), interactive_enabled=True)

        [event] = await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED)
        parsed = UpdateEvent.parse(event)
        assert (parsed.bot_code, parsed.update_id, parsed.chat_id, parsed.user_id, parsed.message_ref) == (
            bot.code,
            10,
            555,
            777,
            "4521",
        )
        assert parsed.attachments[0].file_ref == "ref1"
        assert parsed.caption == "полож"
        assert event["event_id"]
        assert await queued(database, bot) == []
        async with database.session() as session:
            assert await NotificationRepository(session).cursor(bot.id) == 10

    async def test_start_with_a_token_is_the_automations_business(self, stand) -> None:
        database, bot, _ = stand

        await accept(database, bot, photo_update(update_id=11, text="/start abc"), interactive_enabled=True)

        [event] = await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED)
        assert event["text"] == "/start abc"
        assert await outbox(database, bot, NotificationTopics.TELEGRAM_COMMAND_RECEIVED) == []
        async with database.session() as session:
            assert not list(await session.scalars(select(SubscriptionModel).where(SubscriptionModel.bot_id == bot.id)))

    async def test_a_callback_travels_with_its_id(self, stand) -> None:
        database, bot, _ = stand
        update = dataclasses.replace(
            photo_update(update_id=12), kind="callback", attachments=(), callback=Callback("cb-1", "sc:o:x:p", "4530")
        )

        await accept(database, bot, update, interactive_enabled=True)

        [event] = await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED)
        assert event["kind"] == "callback"
        assert event["callback"] == {"id": "cb-1", "data": "sc:o:x:p", "message_ref": "4530"}

    async def test_a_repeated_update_is_published_once(self, stand) -> None:
        database, bot, _ = stand

        assert await accept(database, bot, photo_update(update_id=13), interactive_enabled=True)
        assert not await accept(database, bot, photo_update(update_id=13), interactive_enabled=True)

        assert len(await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED)) == 1

    async def test_with_the_flag_off_an_interactive_bot_lives_the_old_way(self, stand) -> None:
        database, bot, _ = stand

        await accept(database, bot, photo_update(update_id=14), interactive_enabled=False)

        assert await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED) == []
        [message] = await queued(database, bot)
        assert "персональную ссылку" in message.text


class TestOldBotsUnchanged:
    """Флаг включён, но бот обычный: подписки, ответы и команды как до v1.1."""

    async def test_a_photo_to_an_old_bot_gets_the_no_token_answer(self, stand) -> None:
        database, _, plain = stand

        await accept(database, plain, photo_update(update_id=20), interactive_enabled=True)

        assert await outbox(database, plain, NotificationTopics.TELEGRAM_UPDATE_RECEIVED) == []
        [message] = await queued(database, plain)
        assert "персональную ссылку" in message.text
        assert (message.kind, message.buttons, message.reply_to, message.target_ref) == ("message", None, None, None)

    async def test_start_with_an_invite_still_subscribes(self, stand) -> None:
        database, _, plain = stand
        seller_id = uuid.uuid4()
        async with database.session() as session:
            invite = await SubscriptionService(session, NotificationRepository(session)).create_invite(
                plain, seller_id=seller_id, seller_name="ООО Ромашка"
            )

        await accept(
            database, plain, photo_update(update_id=21, text=f"/start {invite.token}"), interactive_enabled=True
        )

        async with database.session() as session:
            assert await NotificationRepository(session).subscribers(plain.id, seller_id) == [555]
        [command] = await outbox(database, plain, NotificationTopics.TELEGRAM_COMMAND_RECEIVED)
        assert command["command"] == "start"

    async def test_a_command_still_goes_to_command_received(self, stand) -> None:
        database, _, plain = stand

        await accept(database, plain, photo_update(update_id=22, text="/returns"), interactive_enabled=True)

        [command] = await outbox(database, plain, NotificationTopics.TELEGRAM_COMMAND_RECEIVED)
        assert command["command"] == "returns"
        assert set(command) == {
            "bot",
            "chat_id",
            "command",
            "argument",
            "text",
            "sellers",
            "username",
            "first_name",
            "event_id",
        }

    async def test_the_fetcher_passes_v11_fields_through(self, stand) -> None:
        database, bot, _ = stand

        class Relay:
            async def updates(self, **_: object) -> list[RelayUpdate]:
                return [
                    RelayUpdate(
                        event_id=30,
                        recipient="555",
                        text="",
                        external_id="777",
                        username="ivan",
                        display_name="Иван",
                        chat_type="private",
                        message_ref="9",
                        attachments=(Attachment(kind="document", file_ref="d1", mime="image/png"),),
                    )
                ]

        fetcher = UpdateFetcher(database, Relay(), bot, invite_ttl_hours=72, wait_seconds=0, interactive_enabled=True)  # type: ignore[arg-type]
        assert await fetcher.fetch_once() == 1
        [event] = await outbox(database, bot, NotificationTopics.TELEGRAM_UPDATE_RECEIVED)
        assert event["attachments"][0]["mime"] == "image/png"
        assert event["chat_type"] == "private"


class RecordingRelay(RelayClient):
    def __init__(self, *, interactive: bool = True) -> None:
        super().__init__(dataclasses.replace(SETTINGS.relay, interactive_enabled=interactive))
        self.calls: list[tuple[str, dict]] = []

    async def send(self, **kwargs) -> str | None:  # type: ignore[override]
        self.calls.append(("send", kwargs))
        return "900"

    async def edit_message(self, **kwargs) -> None:  # type: ignore[override]
        self._require_interactive()
        self.calls.append(("edit", kwargs))

    async def answer_callback(self, **kwargs) -> None:  # type: ignore[override]
        self._require_interactive()
        self.calls.append(("answer", kwargs))


def request(bot: Bot, **fields) -> MessageRequest:
    payload = {
        "message_id": str(uuid.uuid4()),
        "bot": bot.code,
        "audience": {"type": "chat", "chat_id": 555},
        "template": "screens.received",
        "dedupe_key": f"t:{uuid.uuid4()}",
        "params": {},
        **fields,
    }
    return MessageRequest.parse(payload)


async def queue_and_deliver(database: Database, bot: Bot, relay: RelayClient, *requests: MessageRequest) -> None:
    async with database.session() as session:
        repository = NotificationRepository(session)
        dispatch = DispatchService(session, repository, BotRegistry(session, repository))
        for item in requests:
            result = await dispatch.queue(item)
            assert result.rejected is None, result.rejected
        await dispatch.deliver_due(relay, max_attempts=3, backoff_seconds=1, bot_id=bot.id)


class TestOutgoing:
    async def test_buttons_and_reply_from_params_reach_the_relay(self, stand) -> None:
        database, bot, _ = stand
        relay = RecordingRelay()

        await queue_and_deliver(database, bot, relay, request(bot, params={"buttons": BUTTONS, "reply_to": "4521"}))

        [(name, call)] = relay.calls
        assert name == "send"
        assert call["buttons"] == BUTTONS
        assert call["reply_to"] == "4521"
        [row] = await queued(database, bot)
        assert (row.status, row.message_ref, row.buttons, row.reply_to) == ("sent", "900", BUTTONS, "4521")

    async def test_a_plain_message_calls_send_with_the_v1_arguments_only(self, stand) -> None:
        database, bot, _ = stand
        relay = RecordingRelay()

        await queue_and_deliver(database, bot, relay, request(bot, template="screens.help"))

        [(_, call)] = relay.calls
        assert set(call) == {"bot_code", "recipient", "text", "idempotency_key", "attachment"}

    async def test_edit_and_callback_answer_go_by_kind(self, stand) -> None:
        database, bot, _ = stand
        relay = RecordingRelay()

        await queue_and_deliver(
            database,
            bot,
            relay,
            request(bot, kind="edit", target_ref="4530", template="screens.outcome_set", params={"outcome_label": "X"}),
            request(
                bot, kind="callback_answer", target_ref="cb-1", template="screens.callback", params={"result": "saved"}
            ),
        )

        assert [name for name, _ in relay.calls] == ["edit", "answer"]
        assert relay.calls[0][1]["message_ref"] == "4530"
        assert relay.calls[0][1]["buttons"] is None
        assert relay.calls[1][1] == {"bot_code": bot.code, "callback_id": "cb-1", "text": "Исход записан"}
        assert {row.status for row in await queued(database, bot)} == {"sent"}

    async def test_with_the_flag_off_edits_fail_with_a_reason_and_buttons_stay_home(self, stand) -> None:
        database, bot, _ = stand
        relay = RecordingRelay(interactive=False)

        await queue_and_deliver(
            database,
            bot,
            relay,
            request(bot, kind="edit", target_ref="4530", template="screens.outcome_set"),
        )

        [row] = await queued(database, bot)
        assert row.status == "failed"
        assert "interactive" in (row.error or "")

    async def test_bad_buttons_are_a_producer_bug(self, stand) -> None:
        database, bot, _ = stand
        async with database.session() as session:
            repository = NotificationRepository(session)
            dispatch = DispatchService(session, repository, BotRegistry(session, repository))
            result = await dispatch.queue(request(bot, params={"buttons": [[{"label": "x", "action": "я" * 40}]]}))
        assert result.rejected and "buttons" in result.rejected

    @pytest.mark.parametrize(
        "fields",
        [
            {"kind": "edit"},
            {
                "kind": "callback_answer",
                "target_ref": "x",
                "audience": {"type": "seller_subscribers", "seller_id": str(uuid.uuid4())},
            },
            {"kind": "photo"},
        ],
    )
    def test_requests_that_cannot_be_delivered_do_not_parse(self, stand, fields: dict) -> None:
        _, bot, _ = stand
        with pytest.raises(ValueError):
            request(bot, **fields)


class TestRegistry:
    async def test_re_registering_without_the_mode_keeps_it(self, stand) -> None:
        database, bot, _ = stand
        async with database.session() as session:
            again = await BotRegistry(session, NotificationRepository(session)).register(
                code=bot.code, title="Новый", invite_link_template=bot.invite_link_template
            )
        assert again.interactive is True

    async def test_the_mode_can_be_switched_explicitly(self, stand) -> None:
        database, bot, _ = stand
        async with database.session() as session:
            again = await BotRegistry(session, NotificationRepository(session)).register(
                code=bot.code, title="", invite_link_template=bot.invite_link_template, interactive=False
            )
        assert again.interactive is False


def test_the_permanent_error_of_a_disabled_relay_is_not_a_dead_chat_marker() -> None:
    from backend.modules.notifications.application.dispatch import DEAD_CHAT_MARKERS
    from backend.modules.notifications.infrastructure.relay.client import INTERACTIVE_DISABLED

    assert not any(marker in INTERACTIVE_DISABLED for marker in DEAD_CHAT_MARKERS)
    assert issubclass(MessengerPermanentError, Exception)
