import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.domain import CommandEvent
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_returns.application import (
    CollectionService,
    CommandService,
    ExtensionService,
    NotificationService,
    PairingCodeInvalidError,
)
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


def extension(session: AsyncSession) -> ExtensionService:
    return ExtensionService(
        session,
        SellerRepository(session),
        ReturnsRepository(session),
        bot_code=BOT,
        timezone=MOSCOW,
        public_base_url="https://test.local",
        download_path="/extension/marketplace-auto-returns.zip",
        pairing_ttl_minutes=15,
        qr_secret="test-secret",
    )


def commands(session: AsyncSession) -> CommandService:
    return CommandService(session, ReturnsRepository(session), extension(session), bot_code=BOT, timezone=MOSCOW)


def notifications(session: AsyncSession) -> NotificationService:
    return NotificationService(
        session,
        SellerRepository(session),
        ReturnsRepository(session),
        BotRegistry(session, NotificationRepository(session)),
        extension(session),
        bot_code=BOT,
        timezone=MOSCOW,
        notify_hours=(9, 12, 15, 18),
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
        active = await ReturnsRepository(session).active_returns(seller)
    assert tracked is not None and tracked.collected_at is not None and tracked.claims_archived_at is not None
    assert sorted(row.status_key for row in active) == [RETURN_READY, RETURN_TRANSIT]

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


async def test_digest_goes_out_once_per_slot_and_only_when_something_changed(
    database: Database, seller: uuid.UUID
) -> None:
    ready_at = NOW - timedelta(days=3, minutes=1)
    await collect(database, seller, [item(1, ready=ready_at), item(2, "В пути в пвз")])
    # 09:00 МСК — первый слот: готовый, едущий и залежавшийся в одном сообщении.
    async with database.session() as session:
        report = await notifications(session).notify(seller, now=NOW)
        sent = await outbox_templates(session, seller)
    assert (report.digest, report.ready, report.transit, report.overdue) == (1, 1, 1, 1)
    assert sent == ["returns.digest"]
    async with database.session() as session:
        [event] = await session.scalars(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller))
    params = event.payload["params"]
    assert (params["ready_total"], params["transit_total"], params["overdue_total"]) == (1, 1, 1)
    assert params["ready"] == [{"address": "посёлок Развилка 52к1", "count": 1}]
    assert "code" in params and params["no_install"] is True

    # Тот же слот через 10 минут и следующий слот без изменений — тишина.
    async with database.session() as session:
        again = await notifications(session).notify(seller, now=NOW + timedelta(minutes=10))
    async with database.session() as session:
        noon = await notifications(session).notify(seller, now=NOW + timedelta(hours=3))
    assert (again.sent, noon.sent) == (0, 0)
    async with database.session() as session:
        logged = await session.scalars(select(NotificationLogModel).where(NotificationLogModel.seller_id == seller))
    assert sorted(row.kind for row in logged) == ["overdue", "ready", "slot", "slot", "transit"]

    # Новый готовый возврат — в ближайший слот, между слотами ничего.
    await collect(database, seller, [item(1, ready=ready_at), item(2, "Готов к выдаче")], now=NOW + timedelta(hours=4))
    async with database.session() as session:
        between = await notifications(session).notify(seller, now=NOW + timedelta(hours=4))
    async with database.session() as session:
        afternoon = await notifications(session).notify(seller, now=NOW + timedelta(hours=6))
    assert (between.sent, afternoon.sent, afternoon.ready) == (0, 1, 1)


async def test_nothing_before_the_first_slot(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [item(2, "В пути в пвз")])
    async with database.session() as session:
        early = await notifications(session).notify(seller, now=NOW - timedelta(hours=1))
    assert early.sent == 0
    async with database.session() as session:
        on_time = await notifications(session).notify(seller, now=NOW)
    assert on_time.sent == 1


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


def command(seller_id: uuid.UUID | None, text: str, chat_id: int = 42) -> CommandEvent:
    name, _, argument = text.partition(" ")
    return CommandEvent(
        event_id=str(uuid.uuid4()),
        bot_code=BOT,
        chat_id=chat_id,
        command=name.lstrip("/"),
        argument=argument,
        text=text,
        sellers=((seller_id, "ИП Возвраты"),) if seller_id else (),
    )


async def chat_replies(session: AsyncSession, chat_id: int) -> list[dict]:
    aggregate = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-chat:{chat_id}")
    rows = await session.scalars(
        select(OutboxEventModel).where(OutboxEventModel.aggregate_id == aggregate).order_by(OutboxEventModel.created_at)
    )
    return [row.payload for row in rows]


async def test_commands_answer_into_the_chat(database: Database, seller: uuid.UUID) -> None:
    await collect(database, seller, [item(1), item(2, "В пути в пвз")], [claim(str(uuid.uuid4()))])
    chat = 4242
    try:
        async with database.session() as session:
            service = commands(session)
            assert await service.handle(command(seller, "/returns", chat), now=NOW) == "returns.list"
            assert await service.handle(command(seller, "/qr", chat), now=NOW) == "returns.code"
            assert await service.handle(command(seller, "/whatever", chat), now=NOW) == "returns.unknown"
            assert await service.handle(command(None, "/returns", chat), now=NOW) == "returns.no_subscription"
            assert await service.handle(command(None, "/help", chat), now=NOW) == "returns.help"
            replies = await chat_replies(session, chat)
        assert [reply["template"] for reply in replies] == [
            "returns.list",
            "returns.code",
            "returns.unknown",
            "returns.no_subscription",
            "returns.help",
        ]
        assert replies[0]["audience"] == {"type": "chat", "chat_id": chat}
        summary = replies[0]["params"]["sellers"][0]
        assert (summary["ready_total"], summary["transit_total"]) == (1, 1)
        assert summary["ready"] == [{"address": "посёлок Развилка 52к1", "count": 1}]
        # Расширения нет — кода нет, и бот говорит, где его взять.
        assert replies[1]["params"] == {"name": "ИП Возвраты", "date": "2026-09-22", "code": None, "no_install": True}
    finally:
        async with database.session() as session:
            aggregate = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-chat:{chat}")
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == aggregate))
            await session.commit()


async def test_foreign_bot_commands_are_ignored(database: Database, seller: uuid.UUID) -> None:
    event = command(seller, "/returns", 4343)
    foreign = replace(event, bot_code="turnover-alerts")
    async with database.session() as session:
        service = commands(session)
        assert await service.handle(foreign, now=NOW) is None
        assert await chat_replies(session, 4343) == []


async def test_extension_pairs_sends_codes_and_answers_the_waiting_chat(database: Database, seller: uuid.UUID) -> None:
    chat = 4444
    try:
        async with database.session() as session:
            pairing = await extension(session).create_pairing_code(seller, chat_id=chat, now=NOW)
            await session.commit()
            code = pairing.code
        assert len(code) == 6 and code.isdigit()

        async with database.session() as session:
            with pytest.raises(PairingCodeInvalidError):
                await extension(session).pair("000000", install_id="chrome-abcdef12", browser="Chrome", now=NOW)
            paired = await extension(session).pair(code, install_id="chrome-abcdef12", browser="Chrome 130", now=NOW)
        assert paired.token.startswith("mar_") and paired.seller_id == seller
        async with database.session() as session:
            with pytest.raises(PairingCodeInvalidError):
                await extension(session).pair(code, install_id="chrome-abcdef12", browser="Chrome", now=NOW)

        # /qr без кода на сегодня ставит задачу расширению; heartbeat её забирает.
        async with database.session() as session:
            assert await commands(session).handle(command(seller, "/qr", chat), now=NOW) == "returns.code"
            [reply] = await chat_replies(session, chat)
        assert reply["params"]["requested"] is True
        async with database.session() as session:
            install = await extension(session).authenticate(paired.token, now=NOW)
            beat = await extension(session).heartbeat(install, state="ok", error=None, now=NOW)
        assert (beat.refresh_code, beat.has_code_today, beat.seller_name) == (True, False, "ИП Возвраты")

        # Код пришёл: сохранён, чат получил ответ, повторный heartbeat задачу не отдаёт.
        async with database.session() as session:
            install = await extension(session).authenticate(paired.token, now=NOW)
            result = await extension(session).ingest_codes(
                install,
                [
                    {
                        "date": "2026-09-22",
                        "code": "41255",
                        "ext_code": "412",
                        "qr": "1_41255_412_1",
                        "ext_qr": "WB|412|xyz",
                    },
                    {"date": "2026-09-23", "code": "913", "ext_code": "", "qr": "WB|913|abc", "ext_qr": ""},
                    {"date": "not-a-date", "code": "1"},
                ],
                now=NOW,
            )
            replies = await chat_replies(session, chat)
        assert (result.accepted, result.replied_chats) == (2, [chat])
        delivered = replies[-1]["params"]
        # Новый формат первым: шестизначный extCode и extQr, старые — только если новых нет.
        assert (delivered["code"], delivered["legacy_code"], delivered["qr"]) == ("412", "41255", "WB|412|xyz")
        # Возвраты ещё не собирали — адресов рядом с кодом пока нет.
        assert delivered["offices"] == []
        assert delivered["qr_url"].startswith(f"https://test.local/api/v1/wb/returns/qr/{seller}/2026-09-22/")
        async with database.session() as session:
            install = await extension(session).authenticate(paired.token, now=NOW)
            beat = await extension(session).heartbeat(install, state="ok", error=None, now=NOW)
            view = await extension(session).view(seller, now=NOW)
            signature = delivered["qr_url"].rsplit("/", 1)[1].removesuffix(".png")
            assert extension(session).verify_qr(seller, date(2026, 9, 22), signature)
            assert not extension(session).verify_qr(seller, date(2026, 9, 23), signature)
            png = await extension(session).qr_png(seller, date(2026, 9, 22))
        assert (beat.refresh_code, beat.has_code_today) == (False, True)
        assert view.has_code_today and len(view.installs) == 1 and view.installs[0].state == "ok"
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

        # Готовый возврат теперь уходит с кодом дня, а рядом с кодом — адреса ПВЗ из отчёта.
        await collect(database, seller, [item(1)])
        async with database.session() as session:
            params = await extension(session).code_params(seller, "ИП Возвраты", date(2026, 9, 22))
        assert params["offices"] == [{"address": "посёлок Развилка 52к1", "count": 1}]
        async with database.session() as session:
            report = await notifications(session).notify(seller, now=NOW)
            sent = await session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.aggregate_id == seller)
                .order_by(OutboxEventModel.created_at)
            )
            digests = [row.payload for row in sent if row.payload["template"] == "returns.digest"]
        assert report.sent == 1 and digests[0]["params"]["code"] == "412" and digests[0]["params"]["qr"] == "WB|412|xyz"

        # Отзыв установки: токен перестаёт работать.
        async with database.session() as session:
            assert await extension(session).revoke(seller, paired.install_id, now=NOW)
        async with database.session() as session:
            from backend.modules.wb_returns.application import ExtensionUnauthorizedError

            with pytest.raises(ExtensionUnauthorizedError):
                await extension(session).authenticate(paired.token, now=NOW)
    finally:
        async with database.session() as session:
            aggregate = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-chat:{chat}")
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == aggregate))
            await session.commit()


async def test_extension_alerts_fire_once_a_day(database: Database, seller: uuid.UUID) -> None:
    async with database.session() as session:
        pairing = await extension(session).create_pairing_code(seller, now=NOW)
        await session.commit()
    async with database.session() as session:
        paired = await extension(session).pair(pairing.code, install_id="yandex-1234567890", browser="Yandex", now=NOW)
    async with database.session() as session:
        install = await extension(session).authenticate(paired.token, now=NOW)
        await extension(session).heartbeat(install, state="needs_login", error="login page", now=NOW)
    # 09:00 МСК без кода на сегодня, сессия слетела: обе строки в одном сообщении, и только раз.
    async with database.session() as session:
        first = await notifications(session).notify(seller, now=NOW)
    async with database.session() as session:
        noon = await notifications(session).notify(seller, now=NOW + timedelta(hours=3))
    assert (first.sent, first.alerts, noon.sent) == (1, 2, 0)
    async with database.session() as session:
        [event] = await session.scalars(select(OutboxEventModel).where(OutboxEventModel.aggregate_id == seller))
    assert len(event.payload["params"]["alerts"]) == 2
    # Через сутки молчания — снова про код на новый день, вход и теперь про молчание.
    async with database.session() as session:
        later = await notifications(session).notify(seller, now=NOW + timedelta(days=1, hours=1))
    assert (later.sent, later.alerts) == (1, 3)
