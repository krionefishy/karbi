import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry, SubscriptionService
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel, SubscriptionModel
from backend.modules.wb_card_documents.application import (
    TEMPLATE_ALERT,
    TEMPLATE_BASELINE,
    TEMPLATE_DIGEST,
    CardDocumentsPublisher,
    CardDocumentsService,
    CollectionService,
    NotificationService,
)
from backend.modules.wb_card_documents.domain import CardDocument, CardDocuments, DeclaredDocument, Verdict
from backend.modules.wb_card_documents.infrastructure.postgres import (
    CardDocumentsRepository,
)
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.wb_card_documents.worker import CardDocumentsWorker

BOT = "card-documents-test"
BASE = load_settings("backend/shared/settings/config.test.yaml")
SETTINGS = replace(BASE, card_documents=replace(BASE.card_documents, notification_bot=BOT))
MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime.now(UTC).replace(microsecond=0)
TODAY = NOW.astimezone(MOSCOW).date()
VERDICT_AT = datetime(2026, 10, 14, 15, 48, 24, tzinfo=UTC)


def card(
    nm_id: int,
    reason: str = "valid",
    *,
    number: str = "ЕАЭС N RU Д-CN.РА09.В.46306/26",
    end: date | None = None,
    vendor_code: str | None = None,
    declared: str | None = None,
) -> CardDocuments:
    status = 1 if reason == "valid" else 2
    document = CardDocument(
        document_id=nm_id,
        document_type=2,
        number=number,
        start=date(2026, 10, 14),
        end=end or TODAY + timedelta(days=1000),
        endless=False,
        verdict=Verdict(reason == "valid", status, reason, VERDICT_AT),
    )
    return CardDocuments(
        nm_id=nm_id,
        vendor_code=vendor_code or f"VK-{nm_id}",
        title=f"Товар {nm_id}",
        subject_name="Воздуходувки",
        has_block=True,
        documents=(document,),
        overall=None,
        exclude_documents=False,
        declared=DeclaredDocument(number=declared),
    )


def day(moment: datetime) -> date:
    return moment.astimezone(MOSCOW).date()


class FakeSource:
    def __init__(self, cards: list[CardDocuments]) -> None:
        self.cards_served = cards
        self.error: Exception | None = None

    async def cards(self, seller_id: str) -> list[CardDocuments]:
        if self.error is not None:
            raise self.error
        return list(self.cards_served)


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
        await session.execute(delete(BotModel).where(BotModel.code == BOT))
        model = SellerModel(name="ИП Документы", catalog_sync_status="success")
        session.add(model)
        session.add(BotModel(code=BOT, title="Документы", invite_link_template="https://t.me/x?start={token}"))
        await session.flush()
        seller_id = model.id
        await CardDocumentsRepository(session).track(seller_id)
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await CardDocumentsRepository(session).purge_seller(seller_id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller_id))
            await session.execute(delete(BotModel).where(BotModel.code == BOT))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


def publisher(session: AsyncSession, bot: str = BOT) -> CardDocumentsPublisher:
    return CardDocumentsPublisher(session, BotRegistry(session, NotificationRepository(session)), bot_code=bot)


async def collect(
    database: Database, seller_id: uuid.UUID, cards: list[CardDocuments], *, now: datetime = NOW, bot: str = BOT
):
    async with database.session() as session:
        return await CollectionService(
            session,
            SellerRepository(session),
            CardDocumentsRepository(session),
            FakeSource(cards),
            publisher(session, bot),
            timezone=MOSCOW,
        ).collect(seller_id, now=now)


def notifications(session: AsyncSession) -> NotificationService:
    return NotificationService(
        session,
        SellerRepository(session),
        CardDocumentsRepository(session),
        publisher(session),
        timezone=MOSCOW,
        digest_hour=10,
        digest_minute=0,
        stale_hours=9,
    )


def board_service(session: AsyncSession) -> CardDocumentsService:
    return CardDocumentsService(
        session,
        SellerRepository(session),
        CardDocumentsRepository(session),
        BotRegistry(session, NotificationRepository(session)),
        SubscriptionService(session, NotificationRepository(session), invite_ttl_hours=72),
        bot_code=BOT,
        timezone=MOSCOW,
        stale_hours=9,
    )


async def outbox(database: Database, seller_id: uuid.UUID) -> list[dict]:
    async with database.session() as session:
        rows = await session.scalars(
            select(OutboxEventModel)
            .where(OutboxEventModel.aggregate_id == seller_id)
            .order_by(OutboxEventModel.created_at)
        )
        return [row.payload for row in rows]


async def test_the_first_collection_is_silent_but_for_one_summary(database: Database, seller: uuid.UUID) -> None:
    result = await collect(database, seller, [card(1, "document_not_found"), card(2), card(3, "documents_invalid")])

    assert (result.baseline, result.cards, result.problems, result.alerts) == (True, 3, 2, 0)
    [event] = await outbox(database, seller)
    assert event["template"] == TEMPLATE_BASELINE
    assert event["bot"] == BOT
    assert event["audience"] == {"type": "seller_subscribers", "seller_id": str(seller)}
    assert (event["params"]["cards"], event["params"]["problems"]) == (3, 2)
    async with database.session() as session:
        tracked = await CardDocumentsRepository(session).tracked(seller)
        states = await CardDocumentsRepository(session).open_states(seller)
    assert tracked is not None and tracked.baseline_at == NOW and tracked.collected_at == NOW
    assert {nm_id: row.state for nm_id, row in states.items()} == {1: "rejected", 2: "ok", 3: "rejected"}


async def test_a_new_problem_alerts_once_and_a_reread_says_nothing(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1, "document_not_found"), card(2)])
    later = NOW + timedelta(hours=3)

    second = await collect(database, seller, [card(1, "document_not_found"), card(2, "documents_invalid")], now=later)
    third = await collect(
        database, seller, [card(1, "document_not_found"), card(2, "documents_invalid")], now=later + timedelta(hours=3)
    )
    fourth = await collect(
        database, seller, [card(1, "documents_invalid"), card(2, "documents_invalid")], now=later + timedelta(hours=6)
    )

    assert (second.alerts, third.alerts, fourth.alerts) == (1, 0, 1)
    events = await outbox(database, seller)
    assert [event["template"] for event in events] == [TEMPLATE_BASELINE, TEMPLATE_ALERT, TEMPLATE_ALERT]
    alert = events[1]
    assert (alert["params"]["nm_id"], alert["params"]["state"], alert["params"]["reason"]) == (
        2,
        "rejected",
        "documents_invalid",
    )
    assert alert["dedupe_key"].startswith(f"card-documents:alert:{seller}:2:rejected:")
    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        states = await repository.open_states(seller)
        journal = await repository.transitions(seller)
    assert states[2].since == later
    assert [(row.nm_id, row.from_state, row.to_state, row.alerted) for row in journal] == [
        (1, None, "rejected", False),
        (2, "ok", "rejected", True),
        (1, "rejected", "rejected", True),
    ]


async def test_expiry_warns_on_each_threshold_once(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1, end=TODAY + timedelta(days=60))])

    results = []
    for step, left in enumerate((30, 25, 14, 7, 6, -1)):
        moment = NOW + timedelta(hours=3 * (step + 1))
        results.append(
            (await collect(database, seller, [card(1, end=day(moment) + timedelta(days=left))], now=moment)).alerts
        )

    assert results == [1, 0, 1, 1, 0, 1]
    alerts = (await outbox(database, seller))[1:]
    assert [event["params"]["days_left"] for event in alerts] == [30, 14, 7, -1]
    assert alerts[-1]["params"]["reason"] == "expired"
    assert len({event["dedupe_key"] for event in alerts}) == 4


async def test_a_card_that_left_the_catalog_is_closed_without_a_message(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1, "document_not_found"), card(2)])

    result = await collect(database, seller, [card(2)], now=NOW + timedelta(hours=3))

    assert (result.closed, result.alerts) == (1, 0)
    async with database.session() as session:
        board = await board_service(session).board(seller, now=NOW + timedelta(hours=3))
    assert [item.nm_id for item in board.items] == [2]
    assert [event["template"] for event in await outbox(database, seller)] == [TEMPLATE_BASELINE]


async def test_a_cabinet_connected_before_the_bot_gets_its_summary_once_the_bot_exists(
    database: Database, seller: uuid.UUID
) -> None:
    first = await collect(database, seller, [card(1, "document_not_found"), card(2)], bot="no-such-bot")

    assert (first.baseline, first.summary_sent) == (True, False)
    assert await outbox(database, seller) == []
    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        tracked = await repository.tracked(seller)
        assert (await repository.open_states(seller))[1].state == "rejected"
    assert tracked is not None and tracked.baseline_at is None and tracked.collected_at == NOW

    later = NOW + timedelta(hours=3)
    second = await collect(
        database, seller, [card(1, "document_not_found"), card(2, "documents_invalid")], bot="no-such-bot", now=later
    )
    assert (second.baseline, second.alerts, second.summary_sent) == (True, 0, False)

    third = await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "documents_invalid")],
        now=later + timedelta(hours=3),
    )

    assert (third.baseline, third.summary_sent, third.alerts) == (True, True, 0)
    [event] = await outbox(database, seller)
    assert event["template"] == TEMPLATE_BASELINE
    assert (event["params"]["cards"], event["params"]["problems"]) == (2, 2)
    async with database.session() as session:
        tracked = await CardDocumentsRepository(session).tracked(seller)
        journal = await CardDocumentsRepository(session).transitions(seller)
    assert tracked is not None and tracked.baseline_at == later + timedelta(hours=3)
    assert not any(row.alerted for row in journal)


async def test_the_journal_says_alerted_only_when_the_message_was_queued(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1)])
    async with database.session() as session:
        await session.execute(update(BotModel).where(BotModel.code == BOT).values(is_active=False))
        await session.commit()

    result = await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=3))

    assert result.alerts == 0
    async with database.session() as session:
        [row] = await CardDocumentsRepository(session).transitions(seller)
    assert (row.from_state, row.to_state, row.alerted) == ("ok", "rejected", False)


async def test_the_same_problem_coming_back_alerts_again(database: Database, seller: uuid.UUID) -> None:
    """mismatch → ok → mismatch: WB документ не перепроверял, verdict.createdAt тот же."""
    await collect(database, seller, [card(1)])
    steps = [
        card(1, declared="ЕАЭС N RU Д-CN.РА09.В.00000/26"),
        card(1, declared="ЕАЭС N RU Д-CN.РА09.В.46306/26"),
        card(1, declared="ЕАЭС N RU Д-CN.РА09.В.00000/26"),
    ]

    results = [
        (await collect(database, seller, [step], now=NOW + timedelta(hours=3 * (index + 1)))).alerts
        for index, step in enumerate(steps)
    ]

    assert results == [1, 0, 1]
    alerts = [event for event in await outbox(database, seller) if event["template"] == TEMPLATE_ALERT]
    assert [event["params"]["reason"] for event in alerts] == ["number_mismatch", "number_mismatch"]
    assert alerts[0]["dedupe_key"] != alerts[1]["dedupe_key"]


async def test_a_reread_of_the_same_problem_keeps_the_same_key(database: Database, seller: uuid.UUID) -> None:
    """Повторная доставка того же эпизода не должна давать нового ключа — иначе дубль в чате."""
    await collect(database, seller, [card(1)])
    await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=3))
    async with database.session() as session:
        row = (await CardDocumentsRepository(session).open_states(seller))[1]
    since = row.since

    await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=6))

    async with database.session() as session:
        assert (await CardDocumentsRepository(session).open_states(seller))[1].since == since


async def test_the_board_puts_problems_first_and_counts_everything(database: Database, seller: uuid.UUID) -> None:
    await collect(
        database,
        seller,
        [
            card(1, vendor_code="A"),
            card(2, "document_not_found", vendor_code="B"),
            card(3, end=TODAY + timedelta(days=10), vendor_code="C"),
            card(4, end=TODAY + timedelta(days=3), vendor_code="D"),
        ],
    )

    async with database.session() as session:
        service = board_service(session)
        board = await service.board(seller, now=NOW)
        expiring = await service.board(seller, "expiring", now=NOW)

    assert [(item.nm_id, item.state) for item in board.items] == [
        (2, "rejected"),
        (4, "expiring"),
        (3, "expiring"),
        (1, "ok"),
    ]
    assert (board.summary.total, board.summary.ok, board.summary.rejected, board.summary.expiring) == (4, 1, 1, 2)
    assert (board.collected_at, board.stale, board.last_error) == (NOW, False, None)
    assert board.items[1].days_left == 3 and board.items[1].checked_at == VERDICT_AT
    assert [item.nm_id for item in expiring.items] == [4, 3]
    assert expiring.summary.total == 4


async def test_the_digest_goes_once_a_day_and_only_with_problems(database: Database, seller: uuid.UUID) -> None:
    morning = datetime.combine(TODAY, datetime.min.time(), tzinfo=MOSCOW).replace(hour=9, minute=30)
    await collect(database, seller, [card(1, "document_not_found"), card(2)], now=morning.astimezone(UTC))

    async with database.session() as session:
        early = await notifications(session).watch(seller, now=morning)
    async with database.session() as session:
        due = await notifications(session).watch(seller, now=morning.replace(hour=10, minute=5))
    async with database.session() as session:
        again = await notifications(session).watch(seller, now=morning.replace(hour=15))

    assert (early.digest, due.digest, again.digest) == (False, True, False)
    digest = (await outbox(database, seller))[-1]
    assert digest["template"] == TEMPLATE_DIGEST
    assert digest["dedupe_key"] == f"card-documents:digest:{seller}:{TODAY.isoformat()}"
    assert [item["nm_id"] for item in digest["params"]["items"]] == [1]


async def test_a_quiet_seller_gets_no_digest(database: Database, seller: uuid.UUID) -> None:
    moment = datetime.combine(TODAY, datetime.min.time(), tzinfo=MOSCOW).replace(hour=11)
    await collect(database, seller, [card(1)], now=moment.astimezone(UTC))

    async with database.session() as session:
        report = await notifications(session).watch(seller, now=moment)

    assert not report.digest
    assert [event["template"] for event in await outbox(database, seller)] == [TEMPLATE_BASELINE]


async def notice_kinds(database: Database, seller_id: uuid.UUID, now: datetime = NOW) -> dict[str, tuple]:
    async with database.session() as session:
        board = await board_service(session).board(seller_id, now=now)
    return {notice.kind: (notice.level, notice.since, notice.detail) for notice in board.notices}


async def subscribe(database: Database, seller_id: uuid.UUID, *, active: bool = True, chat_id: int = 900_001) -> None:
    async with database.session() as session:
        bot_id = await session.scalar(select(BotModel.id).where(BotModel.code == BOT))
        session.add(SubscriptionModel(bot_id=bot_id, chat_id=chat_id, seller_id=seller_id, is_active=active))
        await session.commit()


async def test_never_collected_shows_no_stale_banner(database: Database, seller: uuid.UUID) -> None:
    """«Ещё не собирали» показывает фронт по collected_at = null; баннер stale его не дублирует."""
    await subscribe(database, seller)

    assert await notice_kinds(database, seller) == {}


async def test_the_stale_banner_comes_and_goes_with_the_collection(database: Database, seller: uuid.UUID) -> None:
    await subscribe(database, seller)
    collected = NOW - timedelta(hours=10)
    await collect(database, seller, [card(1)], now=collected)

    assert await notice_kinds(database, seller) == {"stale": ("danger", collected, None)}

    await collect(database, seller, [card(1)], now=NOW)

    assert await notice_kinds(database, seller) == {}


async def test_error_banners_tell_temporary_from_permanent_and_clear_on_success(
    database: Database, seller: uuid.UUID
) -> None:
    await subscribe(database, seller)
    source = FakeSource([card(1)])
    worker = CardDocumentsWorker(database, source, SETTINGS)
    assert await worker.collect(seller) is None

    source.error = WBTemporaryError("WB Content API отвечает HTTP 503")
    await worker.collect(seller)
    temporary = await notice_kinds(database, seller, datetime.now(UTC))
    assert set(temporary) == {"collection_error"}
    assert temporary["collection_error"][0] == "danger"
    assert temporary["collection_error"][2] == "WB Content API отвечает HTTP 503"
    assert temporary["collection_error"][1] is not None

    source.error = WBPermanentError("Нет доступа к WB Content API: у ключа нет категории «Контент»")
    await worker.collect(seller)
    permanent = await notice_kinds(database, seller, datetime.now(UTC))
    assert set(permanent) == {"permanent_error"}
    assert permanent["permanent_error"][2] == "Нет доступа к WB Content API: у ключа нет категории «Контент»"

    source.error = None
    assert await worker.collect(seller) is None
    assert await notice_kinds(database, seller, datetime.now(UTC)) == {}
    assert [event["template"] for event in await outbox(database, seller)] == [TEMPLATE_BASELINE]


async def test_subscriber_and_bot_banners_follow_the_notifications_module(
    database: Database, seller: uuid.UUID
) -> None:
    await collect(database, seller, [card(1)])

    assert await notice_kinds(database, seller) == {"no_subscribers": ("warning", None, None)}

    await subscribe(database, seller, active=False, chat_id=900_002)
    assert set(await notice_kinds(database, seller)) == {"no_subscribers"}

    await subscribe(database, seller, chat_id=900_003)
    assert await notice_kinds(database, seller) == {}

    async with database.session() as session:
        await session.execute(update(BotModel).where(BotModel.code == BOT).values(is_active=False))
        await session.commit()
    assert await notice_kinds(database, seller) == {"bot_missing": ("warning", None, BOT)}

    async with database.session() as session:
        await session.execute(update(BotModel).where(BotModel.code == BOT).values(is_active=True))
        await session.commit()
    assert await notice_kinds(database, seller) == {}


async def test_alerts_still_go_to_subscribers(database: Database, seller: uuid.UUID) -> None:
    await subscribe(database, seller)
    await collect(database, seller, [card(1)])

    result = await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=3))

    assert result.alerts == 1
    events = await outbox(database, seller)
    assert [event["template"] for event in events] == [TEMPLATE_BASELINE, TEMPLATE_ALERT]
    assert all(event["audience"] == {"type": "seller_subscribers", "seller_id": str(seller)} for event in events)


async def test_a_failed_collection_keeps_the_states(database: Database, seller: uuid.UUID) -> None:
    source = FakeSource([card(1, "document_not_found")])
    worker = CardDocumentsWorker(database, source, SETTINGS)
    assert await worker.collect(seller) is None

    source.error = WBTemporaryError("WB Content API отвечает HTTP 429")
    source.cards_served = [card(1)]
    assert await worker.collect(seller) == "WB Content API отвечает HTTP 429"

    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        tracked = await repository.tracked(seller)
        states = await repository.open_states(seller)
    assert tracked is not None and tracked.collection_error == "WB Content API отвечает HTTP 429"
    assert tracked.attempted_at is not None and tracked.collected_at is not None
    assert states[1].state == "rejected"
    assert [event["template"] for event in await outbox(database, seller)] == [TEMPLATE_BASELINE]


async def test_the_worker_collects_on_interval_skips_busy_hours_and_serves_the_button(
    database: Database, seller: uuid.UUID
) -> None:
    source = FakeSource([card(1)])
    quiet = datetime.combine(TODAY, datetime.min.time(), tzinfo=MOSCOW).replace(hour=2, minute=40)
    worker = CardDocumentsWorker(database, source, SETTINGS)

    assert worker.due_since(NOW) == NOW - timedelta(hours=SETTINGS.card_documents.interval_hours)
    assert await worker.collect_due(quiet) == 0
    assert await worker.collect_due(quiet.replace(hour=3)) >= 1
    async with database.session() as session:
        tracked = await CardDocumentsRepository(session).tracked(seller)
        assert tracked is not None and tracked.collected_at is not None
        due = await CardDocumentsRepository(session).sellers_due(
            worker.due_since(datetime.now(UTC)), retry_after=datetime.now(UTC)
        )
    assert seller not in due

    async with database.session() as session:
        service = board_service(session)
        first = await service.request_refresh(seller)
        again = await service.request_refresh(seller)
    assert first.requested_at == again.requested_at

    await worker.serve_refresh_requests()
    async with database.session() as session:
        state = await board_service(session).refresh_state(seller)
    assert state is not None and (state.status, state.in_progress) == ("success", False)
