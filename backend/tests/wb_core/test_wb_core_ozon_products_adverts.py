"""Зеркало карточек Ozon и расхода рекламы Ozon по SKU: клиенты и сбор."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest_asyncio
import respx
from sqlalchemy import delete

from backend.modules.wb_core.application import MirrorService, OzonAdvertMirror, OzonProductMirror
from backend.modules.wb_core.domain import MIRROR_OZON_ADVERTS, MIRROR_OZON_PRODUCTS, OzonAdvertCampaign
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    OzonPerformanceClient,
    OzonProductClient,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway
from backend.workers.wb_core.worker import OZON_KINDS

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SELLER = "seller-1"
BINOCULARS, BATTERY = 5755205256, 5201871241


def money(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def product(product_id: int, offer_id: str, sku: int, archived: bool = False) -> dict:
    return {"product_id": product_id, "offer_id": offer_id, "sku": sku, "archived": archived}


async def test_products_are_listed_page_by_page_and_named_from_the_info_call() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            "/v3/product/list",
            reply=lambda payload: (
                (200, {"result": {"items": [product(1, "бинокль_мини", BINOCULARS)], "last_id": "p2", "total": 2}})
                if not payload["body"]["last_id"]
                else (200, {"result": {"items": [product(2, "акб-2", BATTERY, True)], "last_id": "", "total": 2}})
            ),
        )
        stub.on(
            "POST",
            "/v3/product/info/list",
            body={
                "items": [
                    {"id": 1, "name": "Бинокль", "sources": [{"sku": BINOCULARS}]},
                    {"id": 2, "name": "Аккумулятор", "sku": BATTERY, "sources": []},
                ]
            },
        )

        products = await OzonProductClient(make_gateway()).products(SELLER)

    assert sorted((item.sku, item.offer_id, item.name, item.archived) for item in products) == [
        (BATTERY, "акб-2", "Аккумулятор", True),
        (BINOCULARS, "бинокль_мини", "Бинокль", False),
    ]
    info = stub.requests_to("/v3/product/info/list")
    assert len(info) == 1 and info[0]["body"] == {"product_id": [1, 2]} and info[0]["api"] == "seller"


def stat_row(day: str, sku: int, spent: str, orders: str = "1") -> dict:
    return {"date": day, "sku": str(sku), "moneySpent": spent, "views": "10", "clicks": "2", "orders": orders}


async def test_campaign_stats_come_from_an_async_report_per_ten_campaigns() -> None:
    campaigns = [
        OzonAdvertCampaign(index, f"к{index}", "CAMPAIGN_STATE_RUNNING", "SKU", "CPC") for index in range(1, 13)
    ]
    campaigns.append(OzonAdvertCampaign(99, "", "CAMPAIGN_STATE_RUNNING", "REF_BLOGGER", ""))
    # Кампания, закончившаяся до окна, и созданная после него — в отчёт не просятся.
    campaigns.append(
        OzonAdvertCampaign(98, "старая", "CAMPAIGN_STATE_ARCHIVED", "SKU", "CPC", to_date=date(2026, 9, 1))
    )
    campaigns.append(
        OzonAdvertCampaign(97, "новая", "CAMPAIGN_STATE_RUNNING", "SKU", "CPC", created_at=date(2026, 11, 1))
    )
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on("POST", "/api/client/statistics/json", side_effect=[(200, {"UUID": "u1"}), (200, {"UUID": "u2"})])
        stub.on(
            "GET", "/api/client/statistics/u1", side_effect=[(200, {"state": "IN_PROGRESS"}), (200, {"state": "OK"})]
        )
        stub.on("GET", "/api/client/statistics/u2", body={"state": "OK"})
        first_report = {
            "1": {
                "report": {
                    "rows": [
                        stat_row("19.10.2026", BINOCULARS, "17 041,06"),
                        stat_row("19.10.2026", BINOCULARS, "0,94"),
                    ]
                }
            },
            "2": {"report": {"rows": [stat_row("20.10.2026", BATTERY, "9836,27")]}},
        }
        second_report = {"11": {"report": {"rows": [stat_row("21.10.2026", BATTERY, "1,00")]}}}
        stub.on(
            "GET",
            "/api/client/statistics/report",
            reply=lambda payload: (200, first_report if payload["query"]["UUID"] == "u1" else second_report),
        )
        client = OzonPerformanceClient(make_gateway())
        stats = await client.stats(SELLER, campaigns, date(2026, 10, 19), date(2026, 10, 25))

    created = stub.requests_to("/api/client/statistics/json")
    assert [len(call["body"]["campaigns"]) for call in created] == [10, 2]
    assert created[0]["body"] == {
        "campaigns": [str(index) for index in range(1, 11)],
        "dateFrom": "2026-10-19",
        "dateTo": "2026-10-25",
        "groupBy": "DATE",
    }
    assert created[0]["api"] == "performance"
    assert sorted((item.campaign_id, item.day.isoformat(), item.sku, item.money_spent) for item in stats) == [
        (1, "2026-10-19", BINOCULARS, money("17042.00")),
        (2, "2026-10-20", BATTERY, money("9836.27")),
        (11, "2026-10-21", BATTERY, money("1.00")),
    ]


# --- сбор --------------------------------------------------------------------------------


class FakeProducts(OzonProductClient):
    def __init__(self) -> None:
        super().__init__(make_gateway())

    async def products(self, seller_id: str, *, heartbeat=None):
        from backend.modules.wb_core.domain import OzonProduct

        return [OzonProduct(BINOCULARS, 1, "бинокль_мини", "Бинокль", False)]


class FakePerformance(OzonPerformanceClient):
    def __init__(self) -> None:
        super().__init__(make_gateway())
        self.windows: list[tuple[date, date]] = []

    async def campaigns(self, seller_id: str):
        return [OzonAdvertCampaign(1, "к1", "CAMPAIGN_STATE_RUNNING", "SKU", "CPC")]

    async def stats(self, seller_id: str, campaigns, date_from: date, date_to: date, *, heartbeat=None):
        from backend.modules.wb_core.domain import OzonAdvertStat

        self.windows.append((date_from, date_to))
        day = max(date_from, date(2026, 10, 20))
        if day > date_to:
            return []
        return [OzonAdvertStat(1, day, BINOCULARS, 10, 2, 1, money("100.00"), money("1000.00"))]


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
        model = SellerModel(
            name="ИП Ozon карточки",
            catalog_sync_status="success",
            egress_status="undelivered",
            ozon_egress_status="verified",
        )
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


def mirror(database: Database, performance: FakePerformance) -> MirrorService:
    gateway = make_gateway()
    return MirrorService(
        database,
        content=WBContentClient(gateway),
        analytics=WBAnalyticsClient(gateway),
        marketplace=WBMarketplaceClient(gateway),
        feedbacks=WBFeedbackClient(gateway),
        chats=WBChatClient(gateway),
        ozon_products=FakeProducts(),
        ozon_performance=performance,
        ozon_history_from=date(2026, 9, 1),
        ozon_adverts_windows_per_run=2,
        ozon_adverts_overlap_days=3,
    )


async def test_products_and_advert_stats_are_mirrored_by_windows_from_the_cursor(
    database: Database, seller: uuid.UUID
) -> None:
    performance = FakePerformance()
    service = mirror(database, performance)
    now = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)

    products = await service.collect_ozon_products(seller, now=now)
    first = await service.collect_ozon_adverts(seller, now=now)
    second = await service.collect_ozon_adverts(seller, now=now)

    assert products.products == 1
    # Два окна по 31 дню с 1 сентября, затем от курсора с перекрытием в три дня до вчера.
    assert performance.windows[:2] == [(date(2026, 9, 1), date(2026, 10, 1)), (date(2026, 10, 2), date(2026, 10, 27))]
    assert (first.windows, first.collected_through) == (2, date(2026, 10, 26))
    assert performance.windows[2] == (date(2026, 10, 23), date(2026, 10, 27))
    assert (second.windows, second.collected_through) == (1, date(2026, 10, 26))
    async with database.session() as session:
        known = await OzonProductMirror(session).products(seller, [BINOCULARS, 1])
        stats = await OzonAdvertMirror(session).stats(seller, since=date(2026, 10, 19), until=date(2026, 10, 25))
        assert await OzonAdvertMirror(session).collected_through(seller) == date(2026, 10, 26)
        states = MirrorRepository(session)
        products_state = await states.state(seller, MIRROR_OZON_PRODUCTS)
        adverts_state = await states.state(seller, MIRROR_OZON_ADVERTS)
        assert products_state is not None and products_state.collected_at == now
        assert adverts_state is not None and adverts_state.error is None
    assert known[BINOCULARS].offer_id == "бинокль_мини" and 1 not in known
    # Второе окно дало 20.10; третий проход переписал 23–27.10, более ранние дни остались на месте.
    assert sorted((item.day.isoformat(), item.sku, item.money_spent) for item in stats) == [
        ("2026-10-20", BINOCULARS, money("100.00")),
        ("2026-10-23", BINOCULARS, money("100.00")),
    ]
    assert set(OZON_KINDS) == {"ozon_accruals", MIRROR_OZON_PRODUCTS, MIRROR_OZON_ADVERTS}
