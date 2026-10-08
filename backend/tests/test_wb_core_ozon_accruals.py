"""Зеркало начислений Ozon: клиент и сбор по дням от курсора."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
import respx
from sqlalchemy import delete

from backend.modules.wb_core.application import MirrorService, OzonAccrualMirror
from backend.modules.wb_core.domain import MIRROR_OZON_ACCRUALS, OzonAccrualLine
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb import (
    OzonFinanceClient,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBTemporaryError,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import EgressStub, make_gateway
from backend.workers.wb_core.worker import WBCoreWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
SELLER = "seller-1"
BRUSH = 5365042812
BY_DAY = "/v1/finance/accrual/by-day"


def money(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def accrual_sale() -> dict:
    """Продажа FBO с доставкой — как её отдаёт Ozon."""
    return {
        "accrual_id": 64097699624,
        "date": "2026-09-24",
        "total_amount": {"amount": "1118.85", "currency": "RUB"},
        "unit_number": "0238482108-0061-1",
        "accrued_category": "POSTING",
        "posting": {
            "delivery_schema": "Fbo",
            "products": [
                {
                    "sku": 2468858781,
                    "delivery": {
                        "total_accrued": {"amount": "-84.99", "currency": "RUB"},
                        "services": [
                            {"type_id": 32, "accrued": {"amount": "-75", "currency": "RUB"}},
                            {"type_id": 29, "accrued": {"amount": "-9.99", "currency": "RUB"}},
                        ],
                    },
                    "commission": {
                        "seller_price": {"amount": "2508", "currency": "RUB"},
                        "sale_price": {"amount": "1142.1", "currency": "RUB"},
                        "sale_commission": {"amount": "-1304.16", "currency": "RUB"},
                        "commission": {"amount": "-1304.16", "currency": "RUB"},
                        "commission_ratio": 'value:"0.520000"',
                        "sale_amount": {"amount": "2508", "currency": "RUB"},
                        "coinvestment": {"amount": "11.42", "currency": "RUB"},
                        "bonus": {"amount": "1354.48", "currency": "RUB"},
                    },
                    "quantity": 1,
                }
            ],
        },
        "item_fees": None,
        "non_item_fee": None,
        "container_fees": None,
    }


def accrual_item() -> dict:
    return {
        "accrual_id": 64087649064,
        "date": "2026-09-24",
        "total_amount": {"amount": "-20.63", "currency": "RUB"},
        "unit_number": "58007616-0848",
        "accrued_category": "ITEM",
        "posting": None,
        "item_fees": {
            "fees": [{"sku": BRUSH, "fees": [{"type_id": 1, "accrued": {"amount": "-20.63"}}], "quantity": 1}]
        },
        "non_item_fee": None,
        "container_fees": None,
    }


def accrual_non_item() -> dict:
    return {
        "accrual_id": 64089706955,
        "date": "2026-09-24",
        "total_amount": {"amount": "-28433.34", "currency": "RUB"},
        "accrued_category": "NON_ITEM",
        "posting": None,
        "item_fees": None,
        "non_item_fee": {"type_id": 41, "accrued": {"amount": "-28433.34", "currency": "RUB"}},
        "container_fees": None,
    }


async def test_a_day_of_accruals_is_read_page_by_page_and_unfolded_into_lines() -> None:
    with respx.mock as router:
        stub = EgressStub(router)
        stub.on(
            "POST",
            BY_DAY,
            reply=lambda payload: (
                (200, {"accruals": [accrual_sale(), accrual_item()], "last_id": "page2"})
                if not payload["body"]["last_id"]
                else (200, {"accruals": [accrual_non_item(), {"accrual_id": "bad"}], "last_id": ""})
            ),
        )
        lines = await OzonFinanceClient(make_gateway()).accruals(SELLER, date(2026, 9, 24))

    calls = stub.requests_to(BY_DAY)
    assert [call["body"] for call in calls] == [
        {"date": "2026-09-24", "last_id": ""},
        {"date": "2026-09-24", "last_id": "page2"},
    ]
    assert calls[0]["api"] == "seller"
    assert [(line.accrual_id, line.line_no, line.line, line.sku, line.type_id) for line in lines] == [
        (64097699624, 0, "sale", 2468858781, 0),
        (64097699624, 1, "delivery", 2468858781, 32),
        (64097699624, 2, "delivery", 2468858781, 29),
        (64087649064, 0, "item", BRUSH, 1),
        (64089706955, 0, "non_item", 0, 41),
    ]
    sale = lines[0]
    assert (sale.sale_amount, sale.sale_price, sale.bonus, sale.coinvestment) == (
        money("2508"),
        money("1142.10"),
        money("1354.48"),
        money("11.42"),
    )
    assert (sale.amount, sale.sale_commission, sale.quantity, sale.delivery_schema) == (
        money("-1304.16"),
        money("-1304.16"),
        1,
        "Fbo",
    )
    assert (lines[2].amount, lines[3].amount, lines[4].amount) == (money("-9.99"), money("-20.63"), money("-28433.34"))
    # Запрос ушёл по маршруту Ozon, а не WB.
    assert stub.routes == ["/api/v1/ozon/request", "/api/v1/ozon/request"]


# --- сбор ----------------------------------------------------------------------------


def line(day: date, accrual_id: int, amount: str) -> OzonAccrualLine:
    return OzonAccrualLine(
        accrual_id=accrual_id,
        line_no=0,
        day=day,
        category="NON_ITEM",
        unit_number="",
        delivery_schema="",
        line="non_item",
        sku=0,
        type_id=41,
        quantity=0,
        amount=money(amount),
        sale_amount=money("0"),
        sale_price=money("0"),
        sale_commission=money("0"),
        bonus=money("0"),
        coinvestment=money("0"),
    )


class FakeOzonFinance(OzonFinanceClient):
    def __init__(self, lines: list[OzonAccrualLine], *, failing: bool = False) -> None:
        super().__init__(make_gateway())
        self.rows = lines
        self.failing = failing
        self.days: list[date] = []

    async def accruals(self, seller_id: str, day: date) -> list[OzonAccrualLine]:
        if self.failing:
            raise WBTemporaryError("Ozon Seller API отвечает HTTP 429")
        self.days.append(day)
        return [item for item in self.rows if item.day == day]


def mirror(database: Database, client: OzonFinanceClient, *, days_per_run: int = 14) -> MirrorService:
    gateway = make_gateway()
    return MirrorService(
        database,
        content=WBContentClient(gateway),
        analytics=WBAnalyticsClient(gateway),
        marketplace=WBMarketplaceClient(gateway),
        feedbacks=WBFeedbackClient(gateway),
        chats=WBChatClient(gateway),
        ozon_finance=client,
        ozon_history_from=date(2026, 9, 20),
        ozon_days_per_run=days_per_run,
        ozon_overlap_days=2,
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
        # Ключа WB нет, учётка Ozon проверена: начисления Ozon читаются, зеркало WB — нет.
        model = SellerModel(
            name="ИП Ozon", catalog_sync_status="success", egress_status="undelivered", ozon_egress_status="verified"
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


def at(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


async def test_accruals_are_collected_day_by_day_up_to_yesterday_and_resumed_with_overlap(
    database: Database, seller: uuid.UUID
) -> None:
    rows = [line(date(2026, 9, 21), 1, "-10"), line(date(2026, 9, 24), 2, "-20"), line(date(2026, 9, 26), 3, "-30")]
    client = FakeOzonFinance(rows)
    service = mirror(database, client, days_per_run=4)

    first = await service.collect_ozon_accruals(seller, now=at(27))

    assert client.days == [date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)]
    assert (first.days, first.lines, first.collected_through) == (4, 1, date(2026, 9, 23))
    second = await service.collect_ozon_accruals(seller, now=at(27))
    # Два дня перекрытия, дальше до вчера; сегодня не читается — день ещё идёт.
    assert client.days[4:] == [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
    third = await service.collect_ozon_accruals(seller, now=at(27))
    assert (second.collected_through, third.collected_through, third.days) == (date(2026, 9, 24), date(2026, 9, 25), 4)
    assert (await service.collect_ozon_accruals(seller, now=at(27))).collected_through == date(2026, 9, 26)
    async with database.session() as session:
        port = OzonAccrualMirror(session)
        week = await port.lines(seller, since=date(2026, 9, 21), until=date(2026, 9, 27))
        assert await port.collected_through(seller) == date(2026, 9, 26)
    assert sorted((item.day.isoformat(), item.amount) for item in week) == [
        ("2026-09-21", money("-10")),
        ("2026-09-24", money("-20")),
        ("2026-09-26", money("-30")),
    ]


async def test_a_failed_day_keeps_the_cursor_and_the_days_already_written(
    database: Database, seller: uuid.UUID
) -> None:
    client = FakeOzonFinance([], failing=True)

    with pytest.raises(WBTemporaryError):
        await mirror(database, client).collect_ozon_accruals(seller, now=at(27))

    async with database.session() as session:
        state = await OzonAccrualMirror(session).state(seller)
        assert state is not None and state.collected_at is None and state.error
        assert await OzonAccrualMirror(session).collected_through(seller) is None


async def test_ozon_accruals_are_due_by_the_ozon_key_not_the_wb_one(database: Database, seller: uuid.UUID) -> None:
    service = mirror(database, FakeOzonFinance([]))
    interval = timedelta(minutes=SETTINGS.core_mirror.ozon_interval_minutes)
    worker = WBCoreWorker(database, service, SETTINGS, now=lambda: at(27))

    assert worker.due_since(MIRROR_OZON_ACCRUALS, at(27)) == at(27) - interval
    # Ключа WB у кабинета нет — зеркало WB его не трогает, а Ozon собирается.
    assert await worker.collect_due(MIRROR_OZON_ACCRUALS, at(27)) == 1
    later = WBCoreWorker(database, service, SETTINGS, now=lambda: at(27, hour=7))
    assert await later.collect_due(MIRROR_OZON_ACCRUALS, at(27, hour=7)) == 0
