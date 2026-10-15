import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    MIRROR_CHATS,
    ChatEvent,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError, WBTemporaryError
from backend.modules.wb_support_bot.application import BotPolicy, SupportBotProcessor
from backend.modules.wb_support_bot.infrastructure.postgres import (
    MODE_DRY_RUN,
    MODE_LIVE,
    SEND_DRY_RUN,
    SEND_FAILED,
    SEND_SENT,
    SEND_UNKNOWN,
    DialogModel,
    SendModel,
    SupportBotRepository,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
PROMPT_AT = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)  # 09:00 МСК
CHAT = "1:chat-a"
TEXT = "Здравствуйте! Подскажите, удалось ли решить проблему?"


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.error: Exception | None = None

    async def send(self, seller_id: str, reply_sign: str, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((seller_id, reply_sign, text))


async def no_sleep(_: float) -> None:
    return None


def event(minutes: float, kind: str, text: str = "", *, sign: str | None = "1:sign") -> ChatEvent:
    sender, source, prompt = {
        "prompt": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, True),
        "third_party": (CHAT_SENDER_SELLER, CHAT_SOURCE_API, False),
        "specialist": (CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, False),
        "bot": (CHAT_SENDER_SELLER, CHAT_SOURCE_API, False),
        "buyer": (CHAT_SENDER_CLIENT, "ios", False),
    }[kind]
    return ChatEvent(
        event_id=f"{kind}-{minutes}",
        chat_id=CHAT,
        sender=sender,
        source=source,
        added_at=PROMPT_AT + timedelta(minutes=minutes),
        is_new_chat=prompt,
        review_prompt=prompt,
        nm_id=1304195061 if prompt else None,
        rid=None,
        text=text or None,
        has_attachments=False,
        reply_sign=sign,
    )


BASE_FEED = [
    event(0, "prompt", "Здравствуйте. Вы оставили отзыв с низкой оценкой"),
    event(1, "third_party", "Сообщение выше автоматическое"),
    event(30, "buyer", "."),
    event(60, "specialist", "Опишите, пожалуйста, проблему"),
]
# 14:10 МСК, через 4 часа 10 минут после ответа специалиста: тишина вышла, окно открыто.
NOW = PROMPT_AT + timedelta(minutes=60 + 4 * 60 + 10)


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield database
    finally:
        await database.disconnect()


@pytest_asyncio.fixture
async def seller(database: Database) -> AsyncIterator[uuid.UUID]:
    async with database.session() as session:
        model = SellerModel(name="ИП Бот", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        repository = SupportBotRepository(session)
        await repository.track(seller_id)
        await repository.add_template(seller_id, TEXT)
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await SupportBotRepository(session).purge(seller_id)
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


async def feed(database: Database, seller_id: uuid.UUID, events: list[ChatEvent], *, read_at: datetime) -> None:
    async with database.session() as session:
        mirror = MirrorRepository(session)
        await mirror.record_attempt(seller_id, MIRROR_CHATS, now=read_at)
        await mirror.insert_chat_events(seller_id, events, now=read_at)
        await mirror.save_chat_cursor(seller_id, int(read_at.timestamp() * 1000), tail_reached_at=read_at)
        await mirror.mark_collected(seller_id, MIRROR_CHATS, now=read_at)
        await session.commit()


async def go_live(database: Database, seller_id: uuid.UUID) -> None:
    async with database.session() as session:
        await SupportBotRepository(session).set_mode(seller_id, MODE_LIVE)
        await session.commit()


async def run(database: Database, seller_id: uuid.UUID, sender: FakeSender, *, now: datetime = NOW):
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(),
            rng=random.Random(1),
            sleep=no_sleep,
        )
        return await processor.process_seller(seller_id, now=now)


async def sends(database: Database, seller_id: uuid.UUID) -> list[SendModel]:
    async with database.session() as session:
        return list(
            await session.scalars(select(SendModel).where(SendModel.seller_id == seller_id).order_by(SendModel.seq))
        )


async def dialog(database: Database, seller_id: uuid.UUID) -> DialogModel:
    async with database.session() as session:
        return (await session.scalars(select(DialogModel).where(DialogModel.seller_id == seller_id))).one()


async def test_dry_run_counts_the_send_but_does_not_call_wb(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()

    report = await run(database, seller, sender)

    assert report.campaigns_started == 1 and report.dry_run == 1 and report.sent == 0
    assert sender.calls == []
    rows = await sends(database, seller)
    assert [row.status for row in rows] == [SEND_DRY_RUN] and rows[0].text == TEXT
    state = await dialog(database, seller)
    assert state.state == "in_campaign" and state.sent_count == 1
    assert state.next_send_at is not None and state.next_send_at > NOW + timedelta(hours=1)


async def test_live_sends_once_and_waits_for_the_next_slot(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()

    first = await run(database, seller, sender)
    second = await run(database, seller, sender, now=NOW + timedelta(minutes=10))

    assert first.sent == 1 and second.sent == 0
    assert sender.calls == [(str(seller), "1:sign", TEXT)]
    assert [row.status for row in await sends(database, seller)] == [SEND_SENT]


async def test_nothing_goes_to_a_buyer_who_never_answered(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED[:2], read_at=NOW)
    sender = FakeSender()

    await run(database, seller, sender)

    assert sender.calls == []
    assert (await dialog(database, seller)).state == "no_reply"


async def test_a_buyer_reply_ends_the_campaign(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    await run(database, seller, sender)
    later = NOW + timedelta(hours=2)
    await feed(
        database,
        seller,
        [event(60 + 4 * 60 + 10 + 1, "bot", TEXT), event(60 + 4 * 60 + 60, "buyer", "Хорошо, напишу")],
        read_at=later,
    )

    report = await run(database, seller, sender, now=later)

    assert report.sent == 0
    state = await dialog(database, seller)
    assert state.state == "awaiting_us" and state.next_send_at is None and state.campaign_started_at is None


async def test_a_stale_mirror_blocks_sending(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW - timedelta(hours=5))
    sender = FakeSender()

    report = await run(database, seller, sender)

    assert sender.calls == [] and report.skipped is not None and "устарело" in report.skipped


async def test_outside_the_window_nothing_is_sent(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    night = PROMPT_AT + timedelta(hours=17)  # 23:00 МСК
    await feed(database, seller, BASE_FEED, read_at=night)
    sender = FakeSender()

    report = await run(database, seller, sender, now=night)

    assert sender.calls == [] and report.skipped == "вне окна рассылки"
    # Кампания при этом заведена, и первое сообщение назначено на открытие окна.
    state = await dialog(database, seller)
    assert state.state == "in_campaign" and state.next_send_at is not None and state.next_send_at > night


async def test_the_global_switch_stops_every_seller(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    async with database.session() as session:
        await SupportBotRepository(session).set_paused(True)
        await session.commit()
    sender = FakeSender()
    try:
        report = await run(database, seller, sender)
    finally:
        async with database.session() as session:
            await SupportBotRepository(session).set_paused(False)
            await session.commit()

    assert sender.calls == [] and report.skipped is not None and "выключателем" in report.skipped


async def test_a_transport_failure_is_reconciled_against_the_feed(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    sender.error = WBTemporaryError("шлюз не ответил")

    await run(database, seller, sender)

    assert [row.status for row in await sends(database, seller)] == [SEND_UNKNOWN]
    later = NOW + timedelta(minutes=20)
    await feed(database, seller, [event(60 + 4 * 60 + 11, "bot", TEXT)], read_at=later)
    sender.error = None

    await run(database, seller, sender, now=later)

    assert [row.status for row in await sends(database, seller)] == [SEND_SENT]
    assert sender.calls == []  # повторной отправки не было


async def test_a_message_missing_from_the_feed_is_written_off_as_failed(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    sender.error = WBTemporaryError("шлюз не ответил")
    await run(database, seller, sender)
    later = NOW + timedelta(hours=1)
    await feed(database, seller, [event(60 + 4 * 60 + 15, "specialist", "Позвоните нам")], read_at=later)
    sender.error = None

    await run(database, seller, sender, now=later)

    assert (await sends(database, seller))[0].status == SEND_FAILED


async def test_a_key_without_write_access_pauses_the_seller(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    sender.error = WBPermanentError("WB Buyers Chat API: ключ не имеет доступа к категории «Чат с покупателями»")

    report = await run(database, seller, sender)

    assert report.failed == 1 and report.skipped is not None and "остановлен" in report.skipped
    async with database.session() as session:
        tracked = await SupportBotRepository(session).tracked(seller)
        assert tracked is not None and tracked.paused_reason and tracked.mode == MODE_LIVE
    # Следующий проход ничего не отправляет, пока человек не переведёт кабинет заново.
    sender.error = None
    again = await run(database, seller, sender, now=NOW + timedelta(hours=2))
    assert sender.calls == [] and again.skipped is not None


@pytest.mark.parametrize("mode", [MODE_DRY_RUN])
async def test_a_new_seller_starts_in_dry_run(database: Database, seller: uuid.UUID, mode: str) -> None:
    async with database.session() as session:
        tracked = await SupportBotRepository(session).tracked(seller)
        assert tracked is not None and tracked.mode == mode


def chat_feed(count: int) -> list[ChatEvent]:
    """`count` одинаковых диалогов в разных чатах: покупатель ответил, специалист ответил, тишина вышла."""
    feed_events: list[ChatEvent] = []
    for number in range(count):
        chat = f"1:canary-{number}"
        for minutes, kind, text in (
            (0, "prompt", "Здравствуйте. Вы оставили отзыв с низкой оценкой"),
            (30, "buyer", "."),
            (60, "specialist", "Опишите проблему"),
        ):
            base = event(minutes, kind, text)
            feed_events.append(
                ChatEvent(
                    event_id=f"{base.event_id}-{number}",
                    chat_id=chat,
                    sender=base.sender,
                    source=base.source,
                    added_at=base.added_at,
                    is_new_chat=base.is_new_chat,
                    review_prompt=base.review_prompt,
                    nm_id=None,
                    rid=None,
                    text=base.text,
                    has_attachments=False,
                    reply_sign=f"1:sign-{number}",
                )
            )
    return feed_events


async def test_canary_sends_only_to_its_share_and_dry_runs_the_rest(database: Database, seller: uuid.UUID) -> None:
    from backend.modules.wb_support_bot.domain import in_canary
    from backend.modules.wb_support_bot.infrastructure.postgres import MODE_CANARY

    async with database.session() as session:
        await SupportBotRepository(session).set_mode(seller, MODE_CANARY)
        await session.commit()
    total = 60
    await feed(database, seller, chat_feed(total), read_at=NOW)
    sender = FakeSender()
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(batch=200, canary_percent=10),
            rng=random.Random(1),
            sleep=no_sleep,
        )
        report = await processor.process_seller(seller, now=NOW)

    expected = {f"1:canary-{n}" for n in range(total) if in_canary(f"1:canary-{n}", PROMPT_AT, 10)}
    # диалог относится к канарейке по времени автосообщения: оно у всех одинаковое
    assert report.sent == len(expected) and report.dry_run == total - len(expected)
    assert len(sender.calls) == len(expected)
    assert 0 < len(expected) < total // 2
    rows = await sends(database, seller)
    live_chats = {row.chat_id for row in rows if row.status == SEND_SENT}
    dry_chats = {row.chat_id for row in rows if row.status == SEND_DRY_RUN}
    assert live_chats == expected and live_chats.isdisjoint(dry_chats)
