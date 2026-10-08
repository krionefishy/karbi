"""Зеркало рекламы: клиент рекламного API и сбор окнами от курсора."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete

from backend.modules.wb_core.application import AdvertMirror, MirrorService
from backend.modules.wb_core.domain import MIRROR_ADVERTS, AdvertCampaign, AdvertNmStat, AdvertSpend
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    WBAdvertClient,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBPermanentError,
    WBTemporaryError,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway
from backend.workers.wb_core.worker import WBCoreWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SELLER = "seller-1"
BRUSH, DRILL = 909125353, 1112466805
CAMPAIGN, PAIR = 36113025, 40000001


def upd(day: str, amount: float, *, advert_id: int = CAMPAIGN, payment: str = "Баланс") -> dict:
    return {
        "updTime": f"{day}T23:59:59+03:00",
        "campName": "909125353 - щетка 2",
        "paymentType": payment,
        "updNum": 316880153,
        "updSum": amount,
        "advertId": advert_id,
        "advertType": 9,
        "advertStatus": 11,
        "currency": "RUB",
    }


def nm_block(nm_id: int, amount: float, views: int) -> dict:
    return {"nmId": nm_id, "sum": amount, "views": views, "clicks": 1, "orders": 0, "shks": 0, "atbs": 0, "canceled": 0}


def fullstats(advert_id: int, days: list[tuple[str, list[dict]]]) -> dict:
    return {
        "advertId": advert_id,
        "days": [
            {"date": f"{day}T00:00:00Z", "apps": [{"appType": 32, "nms": nms[:1]}, {"appType": 64, "nms": nms[1:]}]}
            for day, nms in days
        ],
    }


async def test_spend_is_folded_by_moscow_day_and_payment_type() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            "/adv/v1/upd",
            body=[
                upd("2026-09-21", 2),
                {"updTime": "2026-09-21T23:59:59+03:00", "advertId": CAMPAIGN, "paymentType": "Баланс", "updSum": 3},
                upd("2026-09-22", 82, payment="Счет"),
                {"updTime": "2026-09-22T01:30:00Z", "advertId": CAMPAIGN, "paymentType": "Баланс", "updSum": 7},
                {"updTime": None, "advertId": CAMPAIGN, "updSum": 99},
            ],
        )
        spend = await WBAdvertClient(make_gateway()).spend(SELLER, date(2026, 9, 21), date(2026, 9, 27))

    # Две строки одного дня складываются; 01:30 UTC — уже 22-е по Москве.
    assert sorted((item.day.isoformat(), item.payment_type, item.amount) for item in spend) == [
        ("2026-09-21", "Баланс", Decimal("5.00")),
        ("2026-09-22", "Баланс", Decimal("7.00")),
        ("2026-09-22", "Счет", Decimal("82.00")),
    ]
    assert stub.requests_to("/adv/v1/upd")[0]["api"] == "advert"
    with pytest.raises(WBPermanentError):
        await WBAdvertClient(make_gateway()).spend(SELLER, date(2026, 8, 1), date(2026, 9, 27))


async def test_campaigns_and_stats_are_read_in_chunks_of_fifty() -> None:
    ids = list(range(1, 61))
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "GET",
            "/api/advert/v2/adverts",
            body={
                "adverts": [
                    {
                        "id": CAMPAIGN,
                        "bid_type": "unified",
                        "status": 11,
                        "nm_settings": [{"nm_id": BRUSH}, {"nm_id": DRILL}, {"nm_id": "x"}],
                        "settings": {"name": "щетка", "payment_type": "cpm"},
                        "timestamps": {"updated": "2026-10-06T19:32:37.138082+03:00"},
                    },
                    {"id": "bad"},
                ]
            },
        )
        stub.on(
            "GET",
            "/adv/v3/fullstats",
            body=[
                fullstats(
                    CAMPAIGN,
                    [
                        ("2026-09-23", [nm_block(BRUSH, 53.88, 23), nm_block(BRUSH, 28.29, 17)]),
                        ("2026-09-24", [nm_block(DRILL, 1.4, 7)]),
                    ],
                ),
                {"advertId": "bad"},
            ],
        )
        client = WBAdvertClient(make_gateway())
        campaigns = await client.campaigns(SELLER, ids)
        stats = await client.nm_stats(SELLER, ids, date(2026, 9, 21), date(2026, 9, 27))

    assert len(stub.requests_to("/api/advert/v2/adverts")) == 2 and len(stub.requests_to("/adv/v3/fullstats")) == 2
    assert stub.requests_to("/adv/v3/fullstats")[0]["query"]["ids"].count(",") == 49
    [campaign, _] = campaigns
    assert (campaign.advert_id, campaign.nm_ids, campaign.payment_type, campaign.status) == (
        CAMPAIGN,
        (BRUSH, DRILL),
        "cpm",
        11,
    )
    # Площадки одного дня складываются по артикулу.
    assert sorted((item.day.isoformat(), item.nm_id, item.amount, item.views) for item in stats[:2]) == [
        ("2026-09-23", BRUSH, Decimal("82.17"), 40),
        ("2026-09-24", DRILL, Decimal("1.40"), 7),
    ]


# --- сбор ------------------------------------------------------------------------------


class FakeAdverts(WBAdvertClient):
    def __init__(self, spend: list[AdvertSpend], *, failing: bool = False) -> None:
        super().__init__(make_gateway())
        self.rows = spend
        self.failing = failing
        self.windows: list[tuple[date, date]] = []
        self.stat_calls: list[set[int]] = []

    async def spend(self, seller_id: str, date_from: date, date_to: date) -> list[AdvertSpend]:
        if self.failing:
            raise WBTemporaryError("WB Advert API отвечает HTTP 429")
        self.windows.append((date_from, date_to))
        return [item for item in self.rows if date_from <= item.day <= date_to]

    @staticmethod
    def nms(advert_id: int) -> tuple[int, ...]:
        return (BRUSH, DRILL) if advert_id == PAIR else (BRUSH,)

    async def campaigns(self, seller_id: str, advert_ids) -> list[AdvertCampaign]:
        return [
            AdvertCampaign(
                advert_id, "кампания", 9, "cpm", "unified", (BRUSH, DRILL) if advert_id == PAIR else (BRUSH,), None
            )
            for advert_id in sorted(set(advert_ids))
        ]

    async def nm_stats(self, seller_id: str, advert_ids, date_from: date, date_to: date) -> list[AdvertNmStat]:
        self.stat_calls.append(set(advert_ids))
        return [
            AdvertNmStat(item.advert_id, item.day, BRUSH, 10, 1, 0, 0, 0, 0, item.amount, Decimal(0))
            for item in self.rows
            if date_from <= item.day <= date_to and item.advert_id in set(advert_ids)
        ]


def mirror(database: Database, client: WBAdvertClient, *, windows_per_run: int = 3) -> MirrorService:
    gateway = make_gateway()
    return MirrorService(
        database,
        content=WBContentClient(gateway),
        analytics=WBAnalyticsClient(gateway),
        marketplace=WBMarketplaceClient(gateway),
        feedbacks=WBFeedbackClient(gateway),
        chats=WBChatClient(gateway),
        adverts=client,
        adverts_history_from=date(2026, 8, 1),
        adverts_windows_per_run=windows_per_run,
        adverts_overlap_days=3,
    )


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
        model = SellerModel(name="ИП Реклама", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


def at(day: int, month: int = 10, hour: int = 6) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


async def test_adverts_are_collected_in_windows_from_the_history_start_and_resumed_from_the_cursor(
    database: Database, seller: uuid.UUID
) -> None:
    spend = [
        AdvertSpend(CAMPAIGN, date(2026, 8, 5), "Баланс", Decimal("100")),
        AdvertSpend(CAMPAIGN, date(2026, 9, 23), "Счет", Decimal("82")),
        AdvertSpend(PAIR, date(2026, 10, 1), "Баланс", Decimal("40")),
        AdvertSpend(CAMPAIGN, date(2026, 10, 7), "Баланс", Decimal("5")),
    ]
    client = FakeAdverts(spend)
    service = mirror(database, client, windows_per_run=2)

    first = await service.collect_adverts(seller, now=at(7))

    # Два окна по 31 дню от начала истории; курсор — конец второго окна.
    assert client.windows == [(date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 1), date(2026, 10, 1))]
    assert (first.windows, first.spend_rows, first.stat_rows, first.collected_through) == (2, 3, 3, date(2026, 10, 1))
    second = await service.collect_adverts(seller, now=at(7))
    # Следующий проход — с перекрытием в три дня до сегодняшнего дня, курсор не заходит за вчера.
    assert client.windows[-1] == (date(2026, 9, 28), date(2026, 10, 7))
    assert (second.windows, second.collected_through) == (1, date(2026, 10, 6))
    async with database.session() as session:
        port = AdvertMirror(session)
        week = await port.spend(seller, since=date(2026, 9, 28), until=date(2026, 10, 4))
        stats = await port.nm_stats(seller, since=date(2026, 9, 28), until=date(2026, 10, 4))
        campaigns = await port.campaigns(seller, [PAIR, 777])
        assert await port.collected_through(seller) == date(2026, 10, 6)
    assert [(item.advert_id, item.amount) for item in week] == [(PAIR, Decimal("40.00"))]
    assert [(item.nm_id, item.amount) for item in stats] == [(BRUSH, Decimal("40.00"))]
    assert list(campaigns) == [PAIR] and campaigns[PAIR].nm_ids == (BRUSH, DRILL)


async def test_a_failed_advert_window_keeps_the_cursor_and_marks_the_error(
    database: Database, seller: uuid.UUID
) -> None:
    client = FakeAdverts([], failing=True)

    with pytest.raises(WBTemporaryError):
        await mirror(database, client).collect_adverts(seller, now=at(7))

    async with database.session() as session:
        state = await MirrorRepository(session).state(seller, MIRROR_ADVERTS)
        assert state is not None and state.collected_at is None and state.error
        assert await AdvertMirror(session).collected_through(seller) is None


async def test_adverts_are_due_by_interval_without_the_catalog(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database, FakeAdverts([]))
    interval = timedelta(minutes=SETTINGS.core_mirror.adverts_interval_minutes)
    worker = WBCoreWorker(database, service, SETTINGS, now=lambda: at(7))

    assert worker.due_since(MIRROR_ADVERTS, at(7)) == at(7) - interval
    assert await worker.collect_due(MIRROR_ADVERTS, at(7)) == 1
    assert (
        await WBCoreWorker(database, service, SETTINGS, now=lambda: at(7, hour=8)).collect_due(
            MIRROR_ADVERTS, at(7, hour=8)
        )
        == 0
    )
