import io
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import delete, select, update

from backend.app.application import Application
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel, OutboxEventModel, SellerModel
from backend.modules.wb_turnover.application import (
    XLSX_MEDIA_TYPE,
    CollectionService,
    ReplenishmentReportService,
)
from backend.modules.wb_turnover.application.replenishment import DISTRICTS, OTHER_DISTRICT
from backend.modules.wb_turnover.domain import TurnoverRow
from backend.modules.wb_turnover.infrastructure.postgres import (
    RegionOrdersModel,
    TrackedSellerModel,
    TurnoverRepository,
    WarehouseStockModel,
)
from backend.modules.wb_turnover.infrastructure.wb import (
    FBOStockRow,
    OrderRow,
    Warehouse,
    WBAnalyticsClient,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
TODAY = date(2026, 9, 3)
CENTRAL = "Центральный федеральный округ"
VOLGA = "Приволжский федеральный округ"


class FakeStatistics(WBStatisticsClient):
    """Заказы по дням (`flag=1`) и по изменениям (`flag=0`) — как у WB, врозь."""

    def __init__(self, changed=(), by_day=None) -> None:
        super().__init__(make_gateway())
        self.changed = list(changed)
        self.by_day = by_day or {}
        self.asked_days: list[date] = []

    async def orders(self, seller_id: str, date_from: datetime) -> list[OrderRow]:
        return list(self.changed)

    async def orders_on(self, seller_id: str, day: date) -> list[OrderRow]:
        self.asked_days.append(day)
        return list(self.by_day.get(day, ()))


class FakeAnalytics(WBAnalyticsClient):
    def __init__(self, rows=()) -> None:
        super().__init__(make_gateway())
        self.rows = list(rows)

    async def stocks(self, seller_id: str) -> list[FBOStockRow]:
        return list(self.rows)


class FakeMarketplace(WBMarketplaceClient):
    def __init__(self, warehouse_list=(), amounts=None) -> None:
        super().__init__(make_gateway())
        self.warehouse_list = list(warehouse_list)
        self.amounts = amounts or {}

    async def warehouses(self, seller_id: str) -> list[Warehouse]:
        return list(self.warehouse_list)

    async def stocks(self, seller_id: str, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        return {
            chrt_id: amount
            for (warehouse, chrt_id), amount in self.amounts.items()
            if warehouse == warehouse_id and chrt_id in chrt_ids
        }


def order(srid: str, article: str, day: date, district: str, *, cancelled: bool = False) -> OrderRow:
    return OrderRow(
        srid=srid,
        article=article,
        order_date=day,
        last_change_date=datetime.combine(day, datetime.min.time()),
        is_cancel=cancelled,
        price=1000.0,
        warehouse_type="Склад WB",
        district=district,
    )


@pytest_asyncio.fixture
async def seller() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    model = SellerModel(name="Подсорт тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(model)
        await session.flush()
        for article, chrt_id in (("101", 1011), ("102", 1021)):
            session.add(
                ArticleModel(
                    seller_id=model.id,
                    article=article,
                    vendor_code=f"SKU-{article}",
                    name=f"Товар {article}",
                    sizes=[{"chrt_id": chrt_id, "tech_size": "0", "skus": [f"bar-{chrt_id}"]}],
                    state="active",
                )
            )
        session.add(TrackedSellerModel(seller_id=model.id))
        await session.commit()
    try:
        yield database, model.id
    finally:
        async with database.session() as session:
            await TurnoverRepository(session).purge_seller(model.id)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == model.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == model.id))
            await session.commit()
        await database.disconnect()


def collection(session, statistics=None, marketplace=None, analytics=None) -> CollectionService:
    return CollectionService(
        session,
        SellerRepository(session),
        TurnoverRepository(session),
        statistics or FakeStatistics(),
        analytics or FakeAnalytics(),
        marketplace or FakeMarketplace(),
    )


async def regions(database, seller_id, *, since=None, until=TODAY) -> dict[str, dict[str, int]]:
    async with database.session() as session:
        return await TurnoverRepository(session).region_orders(seller_id, since or TODAY - timedelta(days=365), until)


async def collect_regions(database, seller_id, statistics, *, today=TODAY, horizon=180, days=2) -> int:
    async with database.session() as session:
        return await collection(session, statistics).collect_region_orders(
            seller_id, today, horizon_days=horizon, days=days
        )


async def test_the_demand_of_a_district_outlives_the_orders_it_came_from(seller) -> None:
    """Ради этого агрегат и заведён: строки заказов чистятся, доля округа — нет."""
    database, seller_id = seller
    yesterday = TODAY - timedelta(days=1)
    await collect_regions(
        database,
        seller_id,
        FakeStatistics(
            by_day={yesterday: [order("s1", "101", yesterday, CENTRAL), order("s2", "101", yesterday, VOLGA)]}
        ),
        days=1,
    )
    async with database.session() as session:
        await TurnoverRepository(session).prune(
            snapshots_before=TODAY,
            orders_before=TODAY + timedelta(days=1),
            turnover_before=TODAY,
            notifications_before=TODAY,
            regions_before=TODAY - timedelta(days=180),
        )
        await session.commit()

    assert await regions(database, seller_id) == {"101": {CENTRAL: 1, VOLGA: 1}}


async def test_a_cancelled_order_does_not_count_towards_its_district(seller) -> None:
    database, seller_id = seller
    yesterday = TODAY - timedelta(days=1)
    await collect_regions(
        database,
        seller_id,
        FakeStatistics(
            by_day={
                yesterday: [
                    order("s1", "101", yesterday, CENTRAL),
                    order("s2", "101", yesterday, CENTRAL, cancelled=True),
                ]
            }
        ),
        days=1,
    )
    assert await regions(database, seller_id) == {"101": {CENTRAL: 1}}


async def test_an_order_wb_left_without_a_district_still_counts(seller) -> None:
    """Иначе «Итого» в отчёте оказалось бы меньше, чем заказов на самом деле."""
    database, seller_id = seller
    yesterday = TODAY - timedelta(days=1)
    await collect_regions(
        database,
        seller_id,
        FakeStatistics(by_day={yesterday: [order("s1", "101", yesterday, "")]}),
        days=1,
    )
    assert await regions(database, seller_id) == {"101": {"": 1}}


async def test_today_is_not_collected_because_it_is_not_lived_through(seller) -> None:
    database, seller_id = seller
    statistics = FakeStatistics()
    await collect_regions(database, seller_id, statistics, days=1)
    assert statistics.asked_days == [TODAY - timedelta(days=1)]


async def test_history_is_collected_backwards_and_resumes_where_it_stopped(seller) -> None:
    database, seller_id = seller
    days = [TODAY - timedelta(days=offset) for offset in range(1, 5)]
    statistics = FakeStatistics(by_day={day: [order(f"s{index}", "101", day, VOLGA)] for index, day in enumerate(days)})

    assert await collect_regions(database, seller_id, statistics, days=2) == 2
    assert statistics.asked_days == days[:2]

    assert await collect_regions(database, seller_id, statistics, days=2) == 2
    # Третий вызов продолжает с того дня, на котором остановился второй.
    assert statistics.asked_days == days
    assert await regions(database, seller_id) == {"101": {VOLGA: 4}}


async def test_the_walk_stops_at_the_horizon(seller) -> None:
    database, seller_id = seller
    statistics = FakeStatistics()
    # Горизонт в двое суток — это вчера и позавчера, дальше ходить незачем.
    assert await collect_regions(database, seller_id, statistics, horizon=2, days=10) == 2
    assert await collect_regions(database, seller_id, statistics, horizon=2, days=10) == 0
    assert statistics.asked_days == [TODAY - timedelta(days=1), TODAY - timedelta(days=2)]


async def test_a_day_missed_while_the_worker_was_down_is_collected_later(seller) -> None:
    """Отрезок растёт вперёд, поэтому простой не оставляет дыру навсегда."""
    database, seller_id = seller
    statistics = FakeStatistics()
    await collect_regions(database, seller_id, statistics, today=TODAY - timedelta(days=4), days=1)
    statistics.asked_days.clear()

    await collect_regions(database, seller_id, statistics, days=2)

    # Сначала пропущенные сутки от края отрезка, и только потом вглубь.
    assert statistics.asked_days == [TODAY - timedelta(days=4), TODAY - timedelta(days=3)]
    async with database.session() as session:
        tracked = await TurnoverRepository(session).tracked(seller_id)
    assert tracked is not None
    assert (tracked.regions_filled_from, tracked.regions_filled_to) == (
        TODAY - timedelta(days=5),
        TODAY - timedelta(days=3),
    )


async def test_collecting_the_same_day_twice_does_not_double_it(seller) -> None:
    database, seller_id = seller
    yesterday = TODAY - timedelta(days=1)
    rows = [order("a", "101", yesterday, CENTRAL), order("b", "101", yesterday, CENTRAL)]
    for _ in range(2):
        async with database.session() as session:
            await TurnoverRepository(session).replace_region_day(seller_id, yesterday, {})
            await session.execute(
                update(TrackedSellerModel)
                .where(TrackedSellerModel.seller_id == seller_id)
                .values(regions_filled_from=None, regions_filled_to=None)
            )
            await session.commit()
        await collect_regions(database, seller_id, FakeStatistics(by_day={yesterday: rows}), days=1)
    assert await regions(database, seller_id) == {"101": {CENTRAL: 2}}


async def test_fbs_stock_is_kept_per_warehouse_as_well_as_summed(seller) -> None:
    database, seller_id = seller
    marketplace = FakeMarketplace(
        warehouse_list=[Warehouse(7, "ФФ Казань"), Warehouse(8, "ФФ Волгоград")],
        amounts={(7, 1011): 4, (8, 1011): 6, (8, 1021): 1},
    )
    async with database.session() as session:
        result = await collection(session, marketplace=marketplace).collect_stocks(seller_id, TODAY, 0)
    assert result.fbs == "collected"

    async with database.session() as session:
        turnover = TurnoverRepository(session)
        assert await turnover.warehouse_stocks(seller_id) == {"101": {7: 4, 8: 6}, "102": {8: 1}}
        tracked = await turnover.tracked(seller_id)
        assert tracked is not None and tracked.fbs_stocks_at is not None
        # Метрика по-прежнему видит товар целиком, где бы он ни лежал.
        assert (await turnover.latest_stock(seller_id, TODAY))["101"].fbs == 10


async def test_a_warehouse_that_gave_the_goods_back_stops_showing_them(seller) -> None:
    """Слой перезаписывается целиком: обновление по месту оставило бы вчерашнюю цифру."""
    database, seller_id = seller
    async with database.session() as session:
        await collection(
            session,
            marketplace=FakeMarketplace(warehouse_list=[Warehouse(7, "ФФ Казань")], amounts={(7, 1011): 4}),
        ).collect_stocks(seller_id, TODAY, 0)
    async with database.session() as session:
        await collection(
            session, marketplace=FakeMarketplace(warehouse_list=[Warehouse(7, "ФФ Казань")], amounts={})
        ).collect_stocks(seller_id, TODAY, 1)

    async with database.session() as session:
        assert await TurnoverRepository(session).warehouse_stocks(seller_id) == {}
        assert await session.scalar(select(WarehouseStockModel).limit(1)) is None


async def report_rows(database, seller_id):
    service = ReplenishmentReportService(SETTINGS.database.url, window_days=3, region_history_days=180)
    report = await service.build(seller_id, TODAY)
    workbook = load_workbook(filename=io.BytesIO(report.content))
    sheet = workbook["Подсорт"]
    header = [cell.value for cell in sheet[2]]
    rows = [dict(zip(header, [cell.value for cell in row], strict=True)) for row in sheet.iter_rows(min_row=3)]
    return header, {row["Артикул WB"]: row for row in rows}


async def test_the_report_carries_the_three_blocks_the_manager_assembled_by_hand(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await collection(
            session,
            marketplace=FakeMarketplace(warehouse_list=[Warehouse(7, "ФФ Казань")], amounts={(7, 1011): 4}),
        ).collect_stocks(seller_id, TODAY, 0)
    yesterday = TODAY - timedelta(days=1)
    await collect_regions(
        database,
        seller_id,
        FakeStatistics(
            by_day={
                yesterday: [
                    order("s1", "101", yesterday, CENTRAL),
                    order("s2", "101", yesterday, VOLGA),
                    order("s3", "101", yesterday, "Республика Беларусь"),
                ]
            }
        ),
        days=1,
    )
    async with database.session() as session:
        await TurnoverRepository(session).upsert_turnover(
            [
                TurnoverRow(
                    seller_id=seller_id,
                    article="101",
                    date=TODAY,
                    stock_fbo=0,
                    stock_fbs=4,
                    stock_total=4,
                    avg_stock=4.0,
                    orders_count=3,
                    cancelled_count=0,
                    avg_daily_orders=1.5,
                    days_of_cover=2,
                    turnover_days=2,
                    stock_days=1,
                    sales_days=2,
                    status="ok",
                )
            ]
        )
        await session.commit()

    header, rows = await report_rows(database, seller_id)
    assert header[:5] == ["Баркод", "Артикул WB", "Артикул продавца", "Наименование", "Ср. темп 3 дн., шт/день"]
    assert all(district in header for district in DISTRICTS)

    first = rows["101"]
    assert first["Баркод"] == "bar-1011"
    assert first["Ср. темп 3 дн., шт/день"] == 1.5
    assert first[CENTRAL] == 1
    assert first[VOLGA] == 1
    # Заказ из-за границы не пропадает: без него «Итого» не сходилось бы со строкой.
    assert first[OTHER_DISTRICT] == 1
    assert first["Итого"] == 3
    assert first["ФФ Казань"] == 4
    # Метрику по второму артикулу не считали — темп пуст, а не ноль.
    assert rows["102"]["Ср. темп 3 дн., шт/день"] is None
    assert rows["102"]["ФФ Казань"] == 0


async def test_warehouse_columns_stay_empty_until_the_layer_is_collected(seller) -> None:
    """Нули по всему кабинету читались бы как «склады пустые»."""
    database, seller_id = seller
    async with database.session() as session:
        await TurnoverRepository(session).replace_warehouses(seller_id, [(7, "ФФ Казань")])
        await session.commit()

    _, rows = await report_rows(database, seller_id)
    assert rows["101"]["ФФ Казань"] is None


async def test_the_report_refuses_a_seller_that_is_not_there() -> None:
    service = ReplenishmentReportService(SETTINGS.database.url, window_days=3, region_history_days=180)
    with pytest.raises(SellerNotFoundError):
        await service.build(uuid.uuid4(), TODAY)


async def test_purging_a_seller_takes_the_new_tables_with_it(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        turnover = TurnoverRepository(session)
        await turnover.replace_region_day(seller_id, TODAY, {("101", CENTRAL): (1, 0)})
        await turnover.replace_warehouse_stocks(seller_id, {("101", 7): 4})
        await session.commit()
    async with database.session() as session:
        await TurnoverRepository(session).purge_seller(seller_id)
        await session.commit()

    async with database.session() as session:
        assert await session.scalar(select(RegionOrdersModel).limit(1)) is None
        assert await session.scalar(select(WarehouseStockModel).limit(1)) is None


async def test_the_endpoint_hands_the_book_over_as_a_file() -> None:
    """Проверка не столько маршрута, сколько сборки: сервис отчёта получает
    настройки из контейнера, и промах в его регистрации виден только запросом."""
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        token = application.token_service.issue_access(uuid.uuid4())
        seller = SellerModel(name="Подсорт HTTP", catalog_sync_status="success")
        async with application.database.session() as session:
            session.add(seller)
            await session.commit()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                response = await client.get(f"/api/v1/wb/turnover/sellers/{seller.id}/replenishment")
                missing = await client.get(f"/api/v1/wb/turnover/sellers/{uuid.uuid4()}/replenishment")
        finally:
            async with application.database.session() as session:
                await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
                await session.commit()

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert "podsort" in response.headers["content-disposition"]
    assert load_workbook(filename=io.BytesIO(response.content)).sheetnames == ["Подсорт", "Как читать"]
    assert missing.status_code == 404
