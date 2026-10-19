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
        self.list_signs: dict[str, str] = {}
        self.list_reads = 0

    async def reply_signs(self, seller_id: str) -> dict[str, str]:
        self.list_reads += 1
        return self.list_signs

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


async def test_a_dialog_that_left_the_history_window_is_taken_out_of_the_campaign(
    database: Database, seller: uuid.UUID
) -> None:
    old_prompt = NOW - timedelta(days=20)
    async with database.session() as session:
        session.add(
            DialogModel(
                seller_id=seller,
                chat_id="1:old",
                prompt_at=old_prompt,
                state="in_campaign",
                campaign_started_at=old_prompt + timedelta(days=1),
                campaigns_count=1,
                next_send_at=NOW,
            )
        )
        session.add(DialogModel(seller_id=seller, chat_id="1:old2", prompt_at=old_prompt, state="in_campaign"))
        await session.commit()
    await feed(database, seller, BASE_FEED, read_at=NOW)

    await run(database, seller, FakeSender())

    async with database.session() as session:
        rows = {
            row.chat_id: row
            for row in await session.scalars(select(DialogModel).where(DialogModel.seller_id == seller))
        }
    assert rows["1:old"].state == "exhausted" and rows["1:old"].next_send_at is None
    assert rows["1:old2"].state == "awaiting_buyer"


async def test_dialogs_of_removed_products_are_not_written_to(database: Database, seller: uuid.UUID) -> None:
    from backend.modules.wb_core.application import CatalogMirror
    from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel

    async with database.session() as session:
        for article, state in (("1304195061", "archived"), ("999", "active")):
            session.add(ArticleModel(seller_id=seller, article=article, name="Товар", state=state))
        await session.commit()
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(),
            catalog=CatalogMirror(session),
            rng=random.Random(1),
            sleep=no_sleep,
        )
        await processor.process_seller(seller, now=NOW)

    assert sender.calls == []
    assert (await dialog(database, seller)).state == "product_removed"


async def test_an_empty_catalog_does_not_mean_every_product_is_removed(database: Database, seller: uuid.UUID) -> None:
    from backend.modules.wb_core.application import CatalogMirror

    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(),
            catalog=CatalogMirror(session),
            rng=random.Random(1),
            sleep=no_sleep,
        )
        await processor.process_seller(seller, now=NOW)

    assert len(sender.calls) == 1


async def test_a_chat_without_a_stored_sign_gets_it_from_the_chat_list_once(
    database: Database, seller: uuid.UUID
) -> None:
    await go_live(database, seller)
    from dataclasses import replace

    await feed(database, seller, [replace(item, reply_sign=None) for item in BASE_FEED], read_at=NOW)
    sender = FakeSender()
    sender.list_signs = {CHAT: "1:from-list"}

    report = await run(database, seller, sender)

    assert report.sent == 1 and sender.calls == [(str(seller), "1:from-list", TEXT)]
    assert sender.list_reads == 1


async def test_without_any_sign_the_send_is_written_off_with_a_clear_reason(
    database: Database, seller: uuid.UUID
) -> None:
    from dataclasses import replace

    await go_live(database, seller)
    await feed(database, seller, [replace(item, reply_sign=None) for item in BASE_FEED], read_at=NOW)
    sender = FakeSender()

    report = await run(database, seller, sender)

    assert report.failed == 1 and sender.calls == []
    assert (await sends(database, seller))[0].error == "ни лента, ни список чатов не отдали подпись чата"


# --- вердикты нейросети --------------------------------------------------------

from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel  # noqa: E402
from backend.modules.wb_support_bot.domain import MANUAL_ALLOW, MANUAL_DENY  # noqa: E402
from backend.modules.wb_support_bot.infrastructure.postgres import (  # noqa: E402
    VERDICT_DONE,
    VERDICT_FAILED,
    VERDICT_PENDING,
    DialogKey,
    VerdictModel,
)
from backend.shared.kafka_streams.topics import SupportBotTopics  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def clean_classify_outbox(database: Database) -> AsyncIterator[None]:
    yield
    async with database.session() as session:
        await session.execute(
            delete(OutboxEventModel).where(OutboxEventModel.topic == SupportBotTopics.CLASSIFY_REQUESTED)
        )
        await session.commit()


async def run_with(
    database: Database, seller_id: uuid.UUID, sender: FakeSender, mode: str, *, now: datetime = NOW, **policy: object
):
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(classifier_mode=mode, **policy),  # type: ignore[arg-type]
            rng=random.Random(1),
            sleep=no_sleep,
        )
        return await processor.process_seller(seller_id, now=now)


async def verdicts(database: Database, seller_id: uuid.UUID) -> list[VerdictModel]:
    async with database.session() as session:
        return list(await session.scalars(select(VerdictModel).where(VerdictModel.seller_id == seller_id)))


async def classify_events(database: Database, seller_id: uuid.UUID) -> list[OutboxEventModel]:
    async with database.session() as session:
        rows = await session.scalars(
            select(OutboxEventModel).where(OutboxEventModel.topic == SupportBotTopics.CLASSIFY_REQUESTED)
        )
        return [row for row in rows if row.payload.get("seller_id") == str(seller_id)]


async def decide(database: Database, seller_id: uuid.UUID, verdict: str, *, status: str = VERDICT_DONE) -> None:
    async with database.session() as session:
        for row in await session.scalars(select(VerdictModel).where(VerdictModel.seller_id == seller_id)):
            row.status = status
            row.verdict = verdict if status == VERDICT_DONE else None
        await session.commit()


async def test_shadow_mode_asks_the_model_but_does_not_hold_the_send(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)

    report = await run_with(database, seller, FakeSender(), "shadow")
    again = await run_with(database, seller, FakeSender(), "shadow", now=NOW + timedelta(minutes=10))

    assert report.dry_run == 1 and report.held == 0 and report.verdicts_requested == 1
    assert again.verdicts_requested == 0
    rows = await verdicts(database, seller)
    state = await dialog(database, seller)
    assert len(rows) == 1 and rows[0].status == VERDICT_PENDING and rows[0].attempts == 1
    assert rows[0].tail_fingerprint == state.tail_fingerprint and rows[0].tail_event_id == "specialist-60"
    events = await classify_events(database, seller)
    assert len(events) == 1 and events[0].payload["verdict_id"] == str(rows[0].id)


async def test_mode_off_does_not_touch_the_model(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)

    report = await run_with(database, seller, FakeSender(), "off")

    assert report.dry_run == 1 and report.verdicts_requested == 0
    assert await verdicts(database, seller) == [] and await classify_events(database, seller) == []


async def test_enforce_holds_a_dialog_without_a_verdict(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()

    report = await run_with(database, seller, sender, "enforce")

    assert report.held == 1 and report.sent == 0 and sender.calls == []
    assert await sends(database, seller) == []
    state = await dialog(database, seller)
    assert state.next_send_at == NOW + timedelta(minutes=10)


async def test_enforce_sends_once_the_model_says_the_buyer_is_awaited(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    await run_with(database, seller, sender, "enforce")
    await decide(database, seller, "open")

    report = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))

    assert report.sent == 1 and report.held == 0
    assert sender.calls == [(str(seller), "1:sign", TEXT)]


@pytest.mark.parametrize("verdict", ["closing", "unclear"])
async def test_enforce_keeps_holding_when_the_dialog_is_closed_or_unclear(
    database: Database, seller: uuid.UUID, verdict: str
) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    await run_with(database, seller, sender, "enforce")
    await decide(database, seller, verdict)

    report = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))

    assert report.held == 1 and report.sent == 0 and sender.calls == []


async def test_a_failed_verdict_does_not_let_the_message_through(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    await run_with(database, seller, sender, "enforce")
    await decide(database, seller, "open", status=VERDICT_FAILED)

    report = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))

    assert report.held == 1 and sender.calls == []


async def test_a_manual_allow_overrides_a_closing_verdict_and_a_deny_overrides_open(
    database: Database, seller: uuid.UUID
) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    await run_with(database, seller, sender, "enforce")
    await decide(database, seller, "closing")
    state = await dialog(database, seller)
    key = DialogKey(seller, state.chat_id, state.prompt_at)
    async with database.session() as session:
        assert await SupportBotRepository(session).set_manual_verdict(key, MANUAL_ALLOW)
        await session.commit()

    allowed = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))

    assert allowed.sent == 1
    await decide(database, seller, "open")
    async with database.session() as session:
        assert await SupportBotRepository(session).set_manual_verdict(key, MANUAL_DENY)
        await session.commit()
    after_slot = (await dialog(database, seller)).next_send_at
    assert after_slot is not None

    denied = await run_with(database, seller, sender, "enforce", now=after_slot + timedelta(minutes=1))

    assert denied.held == 1 and denied.sent == 0 and len(sender.calls) == 1


async def test_a_manual_decision_expires_when_the_specialist_writes_again(
    database: Database, seller: uuid.UUID
) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "shadow")
    state = await dialog(database, seller)
    async with database.session() as session:
        await SupportBotRepository(session).set_manual_verdict(
            DialogKey(seller, state.chat_id, state.prompt_at), MANUAL_ALLOW
        )
        await session.commit()
    first_tail = (await dialog(database, seller)).tail_fingerprint

    later = BASE_FEED + [event(90, "specialist", "Уточните номер заказа")]
    await feed(database, seller, later, read_at=NOW + timedelta(minutes=30))
    await run_with(database, seller, FakeSender(), "shadow", now=NOW + timedelta(minutes=30))

    after = await dialog(database, seller)
    assert after.tail_fingerprint != first_tail and after.tail_event_id == "specialist-90"
    assert after.manual_verdict is None and after.manual_fingerprint is None


async def test_a_stuck_request_is_asked_again_but_only_a_few_times(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "shadow")
    step = timedelta(minutes=16)

    await run_with(database, seller, FakeSender(), "shadow", now=NOW + step)
    await run_with(database, seller, FakeSender(), "shadow", now=NOW + 2 * step)
    await run_with(database, seller, FakeSender(), "shadow", now=NOW + 3 * step)

    (row,) = await verdicts(database, seller)
    assert row.attempts == 3
    assert len(await classify_events(database, seller)) == 3
