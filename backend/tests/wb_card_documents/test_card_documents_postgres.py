import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry, SubscriptionService
from backend.modules.notifications.application.templates import render
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel, SubscriptionModel
from backend.modules.wb_card_documents.application import (
    TEMPLATE_ALERT,
    TEMPLATE_BASELINE,
    TEMPLATE_DIGEST,
    CardDocumentsPublisher,
    CardDocumentsService,
    CollectionService,
    MirrorStockSource,
    NotificationService,
)
from backend.modules.wb_card_documents.domain import CardDocument, CardDocuments, DeclaredDocument, Verdict
from backend.modules.wb_card_documents.infrastructure.postgres import (
    CardDocumentsRepository,
    TrackedSellerModel,
)
from backend.modules.wb_core.domain import MIRROR_STOCKS, StockFact
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
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
    updated_at: datetime | None = None,
) -> CardDocuments:
    status = 1 if reason == "valid" else 2
    document = CardDocument(
        document_id=f"00000000-0000-4000-8000-{nm_id:012d}",
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
        updated_at=updated_at,
    )


def day(moment: datetime) -> date:
    return moment.astimezone(MOSCOW).date()


class FakeSource:
    """Отдаёт заданные карточки и запоминает, с какого `updatedAt` его спрашивали (None — полный обход)."""

    def __init__(self, cards: list[CardDocuments]) -> None:
        self.cards_served = cards
        self.error: Exception | None = None
        self.calls: list[tuple[datetime, int] | None] = []

    async def cards(self, seller_id: str, *, start_cursor: tuple[datetime, int] | None = None) -> list[CardDocuments]:
        self.calls.append(start_cursor)
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


class FakeStock:
    """Остаток по nmID; карточки нет в словаре — остаток неизвестен."""

    def __init__(self, values: dict[int, int] | None = None) -> None:
        self.values = dict(values or {})

    async def stock(self, seller_id: uuid.UUID, *, now: datetime) -> dict[int, int]:
        return dict(self.values)


def publisher(session: AsyncSession, bot: str = BOT) -> CardDocumentsPublisher:
    return CardDocumentsPublisher(session, BotRegistry(session, NotificationRepository(session)), bot_code=bot)


async def collect(
    database: Database,
    seller_id: uuid.UUID,
    cards: list[CardDocuments],
    *,
    now: datetime = NOW,
    bot: str = BOT,
    source: FakeSource | None = None,
    full: bool = False,
    full_interval: timedelta = timedelta(hours=24),
    stock: dict[int, int] | None = None,
):
    if source is not None:
        source.cards_served = cards
    async with database.session() as session:
        return await CollectionService(
            session,
            SellerRepository(session),
            CardDocumentsRepository(session),
            source or FakeSource(cards),
            publisher(session, bot),
            FakeStock(stock),
            timezone=MOSCOW,
            full_interval=full_interval,
        ).collect(seller_id, now=now, full=full)


def notifications(session: AsyncSession, stock: dict[int, int] | None = None) -> NotificationService:
    return NotificationService(
        session,
        SellerRepository(session),
        CardDocumentsRepository(session),
        publisher(session),
        FakeStock(stock),
        timezone=MOSCOW,
        digest_hour=10,
        digest_minute=0,
        stale_hours=9,
    )


def board_service(session: AsyncSession, stock: dict[int, int] | None = None) -> CardDocumentsService:
    return CardDocumentsService(
        session,
        SellerRepository(session),
        CardDocumentsRepository(session),
        BotRegistry(session, NotificationRepository(session)),
        SubscriptionService(session, NotificationRepository(session), invite_ttl_hours=72),
        bot_code=BOT,
        timezone=MOSCOW,
        stale_hours=9,
        stocks=FakeStock(stock),
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

    assert worker.due_since(NOW) == NOW - timedelta(minutes=SETTINGS.card_documents.interval_minutes)
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


async def test_between_full_passes_only_changed_cards_are_read(database: Database, seller: uuid.UUID) -> None:
    source = FakeSource([])
    first_seen = NOW - timedelta(days=3)
    last_seen = NOW - timedelta(hours=5)
    full = await collect(
        database,
        seller,
        [card(1, updated_at=first_seen), card(2, updated_at=last_seen)],
        source=source,
    )
    assert full.full and source.calls == [None]

    changed_at = NOW + timedelta(minutes=30)
    later = NOW + timedelta(hours=1)
    changed = await collect(
        database, seller, [card(1, "document_not_found", updated_at=changed_at)], source=source, now=later
    )

    assert source.calls[-1] == (last_seen - timedelta(seconds=60), 2)
    assert (changed.full, changed.cards, changed.closed, changed.alerts) == (False, 1, 0, 1)
    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        states = await repository.open_states(seller)
        tracked = await repository.tracked(seller)
    assert {nm_id: row.state for nm_id, row in states.items()} == {1: "rejected", 2: "ok"}
    assert tracked is not None
    assert (tracked.cursor_updated_at, tracked.cursor_nm_id) == (changed_at, 1)
    assert (tracked.full_collected_at, tracked.collected_at) == (NOW, later)

    quiet = await collect(database, seller, [], source=source, now=later + timedelta(hours=1))
    assert (quiet.full, quiet.alerts) == (False, 0)
    assert source.calls[-1] == (changed_at - timedelta(seconds=60), 1)


async def test_a_changed_cards_read_still_moves_expiry_with_the_calendar(database: Database, seller: uuid.UUID) -> None:
    """Порог истечения сдвигает календарь, а не updatedAt: карточку WB не вернёт, а алерт нужен."""
    source = FakeSource([])
    seen = NOW - timedelta(hours=1)
    await collect(
        database,
        seller,
        [card(1, end=day(NOW) + timedelta(days=32), updated_at=seen)],
        source=source,
        full_interval=timedelta(days=10),
    )

    two_days = NOW + timedelta(days=2)
    result = await collect(database, seller, [], source=source, now=two_days, full_interval=timedelta(days=10))

    assert (result.full, result.cards, result.alerts) == (False, 0, 1)
    [alert] = [event for event in await outbox(database, seller) if event["template"] == TEMPLATE_ALERT]
    assert alert["params"]["days_left"] == (day(NOW) + timedelta(days=32) - day(two_days)).days


async def test_the_daily_full_pass_closes_cards_that_left_the_catalog(database: Database, seller: uuid.UUID) -> None:
    source = FakeSource([])
    seen = NOW - timedelta(hours=1)
    await collect(database, seller, [card(1, updated_at=seen), card(2, updated_at=seen)], source=source)

    hourly = await collect(database, seller, [], source=source, now=NOW + timedelta(hours=2))
    daily = await collect(database, seller, [card(2, updated_at=seen)], source=source, now=NOW + timedelta(hours=25))

    assert (hourly.full, hourly.closed) == (False, 0)
    assert (daily.full, daily.closed) == (True, 1)
    assert source.calls[-1] is None
    async with database.session() as session:
        assert set(await CardDocumentsRepository(session).open_states(seller)) == {2}


async def test_the_refresh_button_always_reads_the_whole_catalog(database: Database, seller: uuid.UUID) -> None:
    source = FakeSource([card(1, updated_at=NOW - timedelta(hours=1))])
    worker = CardDocumentsWorker(database, source, SETTINGS)
    assert await worker.collect(seller) is None
    assert await worker.collect(seller) is None
    assert source.calls[0] is None and source.calls[1] is not None

    async with database.session() as session:
        await board_service(session).request_refresh(seller)
    await worker.serve_refresh_requests()

    assert source.calls[-1] is None


async def test_without_a_stored_nm_id_the_read_is_full(database: Database, seller: uuid.UUID) -> None:
    """WB молча игнорирует курсор с nmID 0 (проверено 22.10.2026): без настоящего nmID — полный обход."""
    source = FakeSource([])
    seen = NOW - timedelta(hours=1)
    await collect(database, seller, [card(1, updated_at=seen)], source=source)
    async with database.session() as session:
        await session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller).values(cursor_nm_id=None)
        )
        await session.commit()

    result = await collect(database, seller, [card(1, updated_at=seen)], source=source, now=NOW + timedelta(hours=1))

    assert result.full
    assert source.calls == [None, None]
    async with database.session() as session:
        tracked = await CardDocumentsRepository(session).tracked(seller)
    assert tracked is not None and (tracked.cursor_updated_at, tracked.cursor_nm_id) == (seen, 1)


async def test_the_cursor_keeps_the_latest_card_including_ties(database: Database, seller: uuid.UUID) -> None:
    source = FakeSource([])
    same = NOW - timedelta(hours=1)
    await collect(database, seller, [card(7, updated_at=same), card(3, updated_at=same)], source=source)

    await collect(database, seller, [], source=source, now=NOW + timedelta(hours=1))

    assert source.calls[-1] == (same - timedelta(seconds=60), 7)


def alert_templates(events: list[dict]) -> list[tuple[int, str]]:
    return [
        (event["params"]["nm_id"], event["params"]["state"]) for event in events if event["template"] == TEMPLATE_ALERT
    ]


async def test_a_problem_at_zero_stock_is_not_announced(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1), card(2)], stock={1: 0, 2: 3})

    result = await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found")],
        now=NOW + timedelta(hours=1),
        stock={1: 0, 2: 3},
    )

    assert result.alerts == 1
    assert alert_templates(await outbox(database, seller)) == [(2, "rejected")]
    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        states = await repository.open_states(seller)
        journal = {row.nm_id: row.alerted for row in await repository.transitions(seller)}
    assert (states[1].state, states[1].episode_alerted) == ("rejected", False)
    assert (states[2].state, states[2].episode_alerted) == ("rejected", True)
    assert journal == {1: False, 2: True}


async def test_stock_appearing_on_an_open_problem_announces_it_once(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1)], stock={1: 0})
    await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=1), stock={1: 0})

    appeared = await collect(
        database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=2), stock={1: 5}
    )
    again = await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=3), stock={1: 5})
    gone = await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=4), stock={1: 0})
    back = await collect(database, seller, [card(1, "document_not_found")], now=NOW + timedelta(hours=5), stock={1: 7})

    assert (appeared.alerts, again.alerts, gone.alerts, back.alerts) == (1, 0, 0, 0)
    assert alert_templates(await outbox(database, seller)) == [(1, "rejected")]
    async with database.session() as session:
        repository = CardDocumentsRepository(session)
        row = (await repository.open_states(seller))[1]
        journal = await repository.transitions(seller)
    assert (row.state, row.episode_alerted) == ("rejected", True)
    assert [(entry.from_state, entry.to_state, entry.alerted) for entry in journal] == [
        ("ok", "rejected", False),
        ("rejected", "rejected", True),
    ]


async def test_a_new_episode_after_recovery_is_announced_again(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1)], stock={1: 2})
    steps = [("document_not_found", 1), ("valid", 2), ("document_not_found", 3)]

    results = [
        (await collect(database, seller, [card(1, reason)], now=NOW + timedelta(hours=hours), stock={1: 2})).alerts
        for reason, hours in steps
    ]

    assert results == [1, 0, 1]
    keys = [event["dedupe_key"] for event in await outbox(database, seller) if event["template"] == TEMPLATE_ALERT]
    assert len(set(keys)) == 2


async def test_unknown_stock_does_not_suppress_the_alert(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1), card(2)], stock={2: 0})

    result = await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found")],
        now=NOW + timedelta(hours=1),
        stock={2: 0},
    )

    assert result.alerts == 1
    assert alert_templates(await outbox(database, seller)) == [(1, "rejected")]


async def test_the_first_summary_counts_only_cards_with_stock(database: Database, seller: uuid.UUID) -> None:
    result = await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found"), card(3, "document_not_found"), card(4)],
        stock={1: 4, 2: 0, 4: 0},
    )

    assert result.summary_sent
    [event] = await outbox(database, seller)
    assert (event["params"]["problems"], event["params"]["skipped_without_stock"]) == (2, 1)
    assert "Без остатка пропущено 1" in render(TEMPLATE_BASELINE, event["params"])
    async with database.session() as session:
        states = await CardDocumentsRepository(session).open_states(seller)
    assert {nm_id: row.episode_alerted for nm_id, row in states.items()} == {1: True, 2: False, 3: True, 4: False}

    later = await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found"), card(3, "document_not_found"), card(4)],
        now=NOW + timedelta(hours=1),
        stock={1: 4, 2: 6, 4: 0},
    )
    assert later.alerts == 1
    assert alert_templates(await outbox(database, seller)) == [(2, "rejected")]


async def test_the_digest_lists_only_cards_with_stock(database: Database, seller: uuid.UUID) -> None:
    morning = datetime.combine(TODAY, datetime.min.time(), tzinfo=MOSCOW).replace(hour=9, minute=30)
    await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found"), card(3, "document_not_found")],
        now=morning.astimezone(UTC),
        stock={1: 3, 2: 0},
    )

    async with database.session() as session:
        report = await notifications(session, {1: 3, 2: 0}).watch(seller, now=morning.replace(hour=10, minute=5))

    assert report.digest
    digest = (await outbox(database, seller))[-1]
    assert digest["template"] == TEMPLATE_DIGEST
    assert [item["nm_id"] for item in digest["params"]["items"]] == [1, 3]


async def test_no_digest_when_every_problem_is_out_of_stock(database: Database, seller: uuid.UUID) -> None:
    morning = datetime.combine(TODAY, datetime.min.time(), tzinfo=MOSCOW).replace(hour=9, minute=30)
    await collect(database, seller, [card(1, "document_not_found")], now=morning.astimezone(UTC), stock={1: 0})

    async with database.session() as session:
        report = await notifications(session, {1: 0}).watch(seller, now=morning.replace(hour=10, minute=5))

    assert not report.digest
    assert [event["template"] for event in await outbox(database, seller)] == [TEMPLATE_BASELINE]


async def test_expiry_warnings_wait_for_stock(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [card(1, end=day(NOW) + timedelta(days=60))], stock={1: 0})

    moment = NOW + timedelta(hours=1)
    quiet = await collect(database, seller, [card(1, end=day(moment) + timedelta(days=20))], now=moment, stock={1: 0})
    later = NOW + timedelta(hours=2)
    stocked = await collect(database, seller, [card(1, end=day(later) + timedelta(days=20))], now=later, stock={1: 1})

    assert (quiet.alerts, stocked.alerts) == (0, 1)
    [alert] = [event for event in await outbox(database, seller) if event["template"] == TEMPLATE_ALERT]
    assert alert["params"]["state"] == "expiring"


async def test_the_board_shows_stock_and_counts_problems_worth_a_message(database: Database, seller: uuid.UUID) -> None:
    await collect(
        database,
        seller,
        [card(1, "document_not_found"), card(2, "document_not_found"), card(3, "document_not_found"), card(4)],
    )

    async with database.session() as session:
        board = await board_service(session, {1: 5, 2: 0, 4: 0}).board(seller, now=NOW)

    assert {item.nm_id: item.stock for item in board.items} == {1: 5, 2: 0, 3: None, 4: 0}
    assert (board.summary.rejected, board.summary.with_stock_problems) == (3, 2)


async def test_the_mirror_stock_is_fbo_plus_fbs_and_goes_unknown_when_stale(
    database: Database, seller: uuid.UUID
) -> None:
    source = MirrorStockSource
    async with database.session() as session:
        stocks = source(session, fresh_hours=48)
        assert await stocks.stock(seller, now=NOW) == {}

        mirror = MirrorRepository(session)
        collected = NOW - timedelta(hours=10)
        await mirror.record_attempt(seller, MIRROR_STOCKS, now=collected)
        await mirror.replace_stocks(seller, [StockFact("101", 3, 3, 2), StockFact("102", 0, 0, 0)], {}, now=collected)
        await mirror.mark_collected(seller, MIRROR_STOCKS, now=collected)
        await session.commit()

        assert await stocks.stock(seller, now=NOW) == {101: 5, 102: 0}
        assert await stocks.stock(seller, now=collected + timedelta(hours=49)) == {}
