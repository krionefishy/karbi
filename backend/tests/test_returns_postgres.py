import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_returns.application import CollectionService, NotificationService
from backend.modules.wb_returns.domain import RETURN_READY, RETURN_TRANSIT, Claim, ReturnItem
from backend.modules.wb_returns.infrastructure.postgres import (
    ClaimModel,
    NotificationLogModel,
    ReturnModel,
    ReturnsRepository,
    TrackedSellerModel,
)
from backend.modules.wb_returns.infrastructure.wb import WBClaimsClient, WBReturnsReportClient
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
MOSCOW = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)  # 09:00 МСК
TODAY = NOW.date()
BOT = "wb-returns-test"


def item(
    shk_id: int, status: str = "Готов к выдаче", *, ready: datetime | None = None, active: bool = True
) -> ReturnItem:
    return ReturnItem(
        shk_id=shk_id,
        sticker_id=str(shk_id),
        srid=f"srid-{shk_id}",
        order_id=0,
        nm_id=1271611253,
        barcode="2053497104469",
        brand="KARBI",
        subject_name="Шуруповерты",
        tech_size="0",
        return_type="Возврат брака",
        reason="",
        status=status,
        is_active=active,
        dst_office_id=50041474,
        dst_office_address="посёлок Развилка 52к1",
        order_dt=TODAY - timedelta(days=8),
        ready_to_return_dt=ready,
        expired_dt=None,
        completed_dt=None,
    )


def claim(claim_id: str, *, status: int = 0, archive: bool = False, created: datetime = NOW) -> Claim:
    return Claim(
        id=claim_id,
        claim_type=1,
        status=status,
        status_ex=0,
        nm_id=1152689729,
        imt_name="Бинокль",
        user_comment="не как в описании",
        wb_comment="",
        dt=created,
        order_dt=None,
        dt_update=None,
        delivery_dt=None,
        price=2320.0,
        currency_code="643",
        srid="x",
        photos=("//claim-basket-01.wbbasket.ru/x/1.webp",),
        video_paths=(),
        actions=("approve2",),
        is_archive=archive,
    )


class FakeReport(WBReturnsReportClient):
    def __init__(self, items: list[ReturnItem]) -> None:
        super().__init__(make_gateway())
        self.items = items
        self.calls: list[tuple[date, date]] = []

    async def report(self, seller_id: str, date_from: date, date_to: date) -> list[ReturnItem]:
        self.calls.append((date_from, date_to))
        return list(self.items)


class FakeClaims(WBClaimsClient):
    def __init__(self, open_claims: list[Claim], archived: list[Claim] | None = None) -> None:
        super().__init__(make_gateway())
        self.open_claims = open_claims
        self.archived = archived or []
        self.calls: list[bool] = []

    async def claims(self, seller_id: str, *, archive: bool) -> list[Claim]:
        self.calls.append(archive)
        return list(self.archived if archive else self.open_claims)


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
        model = SellerModel(name="ИП Возвраты", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        bot = BotModel(code=BOT, title="Возвраты", invite_link_template="https://t.me/x?start={token}")
        session.add(bot)
        await session.flush()
        seller_id = model.id
        await ReturnsRepository(session).track(seller_id)
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await ReturnsRepository(session).purge_seller(seller_id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller_id))
            await session.execute(delete(BotModel).where(BotModel.code == BOT))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


async def collect(
    database: Database,
    seller_id: uuid.UUID,
    items: list[ReturnItem],
    open_claims: list[Claim] | None = None,
    archived: list[Claim] | None = None,
    *,
    now: datetime = NOW,
) -> tuple[FakeReport, FakeClaims]:
    report = FakeReport(items)
    claims = FakeClaims(open_claims or [], archived)
    async with database.session() as session:
        await CollectionService(
            session,
            ReturnsRepository(session),
            report,
            claims,
            window_days=14,
            backfill_days=31,
            claims_archive_hours=6,
        ).collect(seller_id, now=now)
    return report, claims


def notifications(session: AsyncSession) -> NotificationService:
    return NotificationService(
        session,
        SellerRepository(session),
        ReturnsRepository(session),
        BotRegistry(session, NotificationRepository(session)),
        bot_code=BOT,
        timezone=MOSCOW,
        digest_hour=9,
        digest_minute=0,
    )


async def outbox_templates(session: AsyncSession, seller_id: uuid.UUID) -> list[str]:
    rows = await session.scalars(
        select(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller_id).order_by(OutboxEventModel.created_at)
    )
    return [row.payload["template"] for row in rows]


async def test_first_collection_reads_month_and_archive_then_two_weeks(database: Database, seller: uuid.UUID) -> None:
    report, claims = await collect(database, seller, [item(1), item(2, "В пути в пвз")], [claim(str(uuid.uuid4()))])
    assert report.calls == [(TODAY - timedelta(days=31), TODAY)]
    assert claims.calls == [False, True]

    async with database.session() as session:
        tracked = await session.get(TrackedSellerModel, seller)
        counts = await ReturnsRepository(session).active_counts(seller)
    assert tracked is not None and tracked.collected_at is not None and tracked.claims_archived_at is not None
    assert counts == {RETURN_READY: 1, RETURN_TRANSIT: 1}

    report, claims = await collect(database, seller, [item(1)], now=NOW + timedelta(hours=1))
    assert report.calls == [(TODAY - timedelta(days=14), TODAY)]
    # Архив читали час назад — до следующего раза ещё пять часов.
    assert claims.calls == [False]


async def test_status_change_is_detected_once(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [item(1, "В пути в пвз")])
    async with database.session() as session:
        changes = await ReturnsRepository(session).upsert_returns(
            seller, [item(1, "Готов к выдаче"), item(1, "Готов к выдаче")], now=NOW + timedelta(hours=2)
        )
        await session.commit()
        stored = await session.get(ReturnModel, (seller, 1))
    # Стикер дважды в одном отчёте — одно изменение, последняя строка побеждает.
    assert [(change.item.shk_id, change.previous_status_key) for change in changes] == [(1, RETURN_TRANSIT)]
    assert stored is not None and stored.status_key == RETURN_READY
    assert stored.status_changed_at == NOW + timedelta(hours=2)
    assert stored.first_seen_at == NOW

    async with database.session() as session:
        again = await ReturnsRepository(session).upsert_returns(seller, [item(1)], now=NOW + timedelta(hours=3))
    assert again == []


async def test_open_claim_gone_from_open_list_is_archived(database: Database, seller: uuid.UUID) -> None:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    await collect(database, seller, [], [claim(first), claim(second)])
    await collect(database, seller, [], [claim(second)], now=NOW + timedelta(hours=1))
    async with database.session() as session:
        stored = await session.get(ClaimModel, (seller, uuid.UUID(first)))
        open_now = await ReturnsRepository(session).open_claims(seller)
    assert stored is not None and stored.is_archive
    assert [str(row.claim_id) for row in open_now] == [second]


async def test_notifications_sent_once_and_by_schedule(database: Database, seller: uuid.UUID) -> None:
    ready_at = NOW - timedelta(days=3, minutes=1)
    claim_id = str(uuid.uuid4())
    await collect(
        database,
        seller,
        [item(1, ready=ready_at), item(2, "В пути в пвз")],
        [claim(claim_id, created=NOW - timedelta(days=4, minutes=1))],
    )
    async with database.session() as session:
        report = await notifications(session).notify(seller, now=NOW)
        sent = await outbox_templates(session, seller)
    # 09:00 МСК: готовый возврат, дайджест по едущему, напоминание на третий день,
    # новая заявка и «завтра истекает срок» по ней же.
    assert (report.ready, report.transit, report.reminders, report.claims, report.claim_deadlines) == (1, 1, 1, 1, 1)
    assert sorted(sent) == sorted(
        ["returns.ready", "returns.transit", "returns.reminder", "returns.claims", "returns.claim_deadline"]
    )

    async with database.session() as session:
        again = await notifications(session).notify(seller, now=NOW + timedelta(minutes=10))
        logged = await session.scalars(select(NotificationLogModel).where(NotificationLogModel.seller_id == seller))
    assert again.sent == 0
    assert sorted(row.kind for row in logged) == ["claim", "claim_deadline", "ready", "reminder", "transit"]

    # Второй день хранения без нового статуса — тихо; пятый — напоминание про утилизацию.
    async with database.session() as session:
        fifth = await notifications(session).notify(seller, now=ready_at + timedelta(days=5, minutes=1))
    assert (fifth.reminders, fifth.ready) == (1, 0)


async def test_digest_waits_for_its_hour(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [item(2, "В пути в пвз")])
    async with database.session() as session:
        early = await notifications(session).notify(seller, now=NOW - timedelta(hours=1))
    assert early.transit == 0
    async with database.session() as session:
        on_time = await notifications(session).notify(seller, now=NOW)
    assert on_time.transit == 1


async def test_missing_bot_leaves_no_trace(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [item(1)])
    async with database.session() as session:
        service = notifications(session)
        service.bot_code = "no-such-bot"
        report = await service.notify(seller, now=NOW)
        logged = list(
            await session.scalars(select(NotificationLogModel).where(NotificationLogModel.seller_id == seller))
        )
    assert report.sent == 0 and logged == []
