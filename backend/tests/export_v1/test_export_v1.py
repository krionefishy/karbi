import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from backend.app.factory import Application
from backend.modules.export_v1.repository import ExportRepository
from backend.modules.wb_card_checklist.infrastructure.postgres.models import CardFactsModel, PriceFactsModel
from backend.modules.wb_core.infrastructure.postgres.models import (
    AdvertCampaignModel,
    AdvertNmStatModel,
    ArticleModel,
    FbsWarehouseStockModel,
    ReviewFactModel,
    SalesReportRowModel,
    SellerModel,
    StockFactModel,
    WarehouseRemainModel,
)
from backend.modules.wb_turnover.infrastructure.postgres.models import OrderModel
from backend.shared.settings import load_settings

TOKEN = "test-export-token-0123456789abcdef"
NEXT_TOKEN = "test-export-next-0123456789abcdef"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
SELLER_ID = uuid.UUID("f03b4256-7bf1-47ad-bbd9-f93e9dd86d80")
STAMP = datetime(2026, 11, 14, 8, 30, tzinfo=UTC)


@dataclass(slots=True)
class Stand:
    app: FastAPI
    client: AsyncClient
    application: Application


@pytest_asyncio.fixture
async def stand() -> AsyncIterator[Stand]:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    settings = replace(settings, app=replace(settings.app, trust_proxy_headers=True))
    application = Application(settings)
    app = application.get_app()
    async with app.router.lifespan_context(app):
        async with application.database.session() as session:
            seller = SellerModel(
                id=SELLER_ID,
                name="ИП Экспорт",
                egress_status="verified",
                ozon_egress_status="undelivered",
                created_at=STAMP,
                updated_at=STAMP,
            )
            session.add(seller)
            await session.flush()
            first = ArticleModel(
                id=uuid.UUID("64ef0308-d929-4fe7-8357-d45f37edcf28"),
                seller_id=SELLER_ID,
                article="1001",
                vendor_code="SKU-1",
                name="Товар один",
                brand="Марка",
                subject_name="Предмет",
                sizes=[
                    {"chrt_id": 11, "tech_size": "S", "skus": ["460001", "460002"]},
                    {"chrt_id": 12, "tech_size": "M", "skus": ["460003"]},
                ],
                updated_at=STAMP,
            )
            second = ArticleModel(
                id=uuid.UUID("74ef0308-d929-4fe7-8357-d45f37edcf28"),
                seller_id=SELLER_ID,
                article="1002",
                vendor_code="SKU-2",
                name="Товар два",
                sizes=[{"chrt_id": 21, "tech_size": "S", "skus": ["470001"]}],
                updated_at=STAMP,
            )
            session.add_all(
                [
                    first,
                    second,
                    CardFactsModel(
                        seller_id=SELLER_ID,
                        article="1001",
                        title="Карточка один",
                        barcode="460001",
                        collected_at=STAMP,
                    ),
                    OrderModel(
                        srid="export-order-1",
                        seller_id=SELLER_ID,
                        article="1001",
                        barcode="460001",
                        order_date=date(2026, 11, 13),
                        last_change_date=STAMP,
                        price=Decimal("123.45"),
                        warehouse_type="Склад WB",
                        collected_at=STAMP,
                    ),
                    SalesReportRowModel(
                        seller_id=SELLER_ID,
                        rrd_id=900001,
                        report_id=700001,
                        doc_type_name="Продажа",
                        seller_oper_name="Продажа",
                        srid="export-order-1",
                        rr_date=date(2026, 11, 13),
                        nm_id=1001,
                        vendor_code="SKU-1",
                        sku="460001",
                        quantity=1,
                        retail_price_withdisc=Decimal("123.45"),
                        retail_amount=Decimal("123.45"),
                        spp=Decimal("15.00"),
                        commission_percent=Decimal("23.00"),
                        for_pay=Decimal("80.00"),
                        delivery_service=Decimal("6.50"),
                        paid_storage=Decimal("0.30"),
                        collected_at=STAMP,
                    ),
                    PriceFactsModel(
                        seller_id=SELLER_ID,
                        article="1001",
                        price=Decimal("2500.00"),
                        discounted_price=Decimal("1750.00"),
                        discount=30,
                        club_discount=5,
                        club_discounted_price=Decimal("1662.50"),
                        collected_at=STAMP,
                    ),
                    AdvertCampaignModel(
                        seller_id=SELLER_ID,
                        advert_id=81001,
                        name="Кампания",
                        status=9,
                        payment_type="cpm",
                        bid_type="unified",
                        nm_ids=[1001, 1002],
                        collected_at=STAMP,
                    ),
                    AdvertNmStatModel(
                        seller_id=SELLER_ID,
                        advert_id=81001,
                        day=date(2026, 11, 13),
                        nm_id=1001,
                        views=100,
                        clicks=10,
                        orders=2,
                        atbs=5,
                        amount=Decimal("42.10"),
                        orders_amount=Decimal("246.90"),
                        collected_at=STAMP,
                    ),
                    WarehouseRemainModel(
                        seller_id=SELLER_ID,
                        barcode="460001",
                        warehouse_name="Коледино",
                        article="1001",
                        quantity=17,
                        collected_at=STAMP,
                    ),
                    FbsWarehouseStockModel(
                        seller_id=SELLER_ID,
                        article="1001",
                        warehouse_id=501,
                        quantity=8,
                        collected_at=STAMP,
                    ),
                    StockFactModel(
                        seller_id=SELLER_ID,
                        article="1001",
                        fbo_quantity=17,
                        fbo_quantity_full=17,
                        fbs_quantity=8,
                        collected_at=STAMP,
                    ),
                    ReviewFactModel(
                        seller_id=SELLER_ID,
                        article="1001",
                        count_rating_1=1,
                        count_rating_2=2,
                        count_rating_3=3,
                        count_rating_4=4,
                        count_rating_5=50,
                        count_with_photo=12,
                        count_with_video=3,
                        wb_rating=Decimal("4.83"),
                        collected_at=STAMP,
                    ),
                ]
            )
            await session.commit()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            try:
                yield Stand(app, client, application)
            finally:
                async with application.database.session() as session:
                    for model in (
                        OrderModel,
                        PriceFactsModel,
                        CardFactsModel,
                        AdvertNmStatModel,
                        AdvertCampaignModel,
                        WarehouseRemainModel,
                        FbsWarehouseStockModel,
                        StockFactModel,
                        ReviewFactModel,
                        SalesReportRowModel,
                        ArticleModel,
                        SellerModel,
                    ):
                        if hasattr(model, "seller_id"):
                            await session.execute(delete(model).where(model.seller_id == SELLER_ID))
                        else:
                            await session.execute(delete(model).where(model.id == SELLER_ID))
                    await session.commit()


async def test_catalog_has_exactly_the_nine_required_datasets_and_both_tokens_work(stand: Stand) -> None:
    response = await stand.client.get("/export/v1/datasets", headers=HEADERS)
    rotated = await stand.client.get("/export/v1/datasets", headers={"Authorization": f"Bearer {NEXT_TOKEN}"})

    assert response.status_code == rotated.status_code == 200
    assert response.json() == rotated.json()
    assert response.json()["contract"] == "export_v1"
    assert [item["name"] for item in response.json()["datasets"]] == [
        "sellers",
        "products",
        "orders",
        "sales_rows",
        "prices",
        "ad_campaigns",
        "ad_stats_daily",
        "stocks",
        "ratings",
    ]
    assert "seller_tax_rates" not in response.text


async def test_auth_network_errors_and_request_errors_use_the_stable_envelope(stand: Stand) -> None:
    missing = await stand.client.get("/export/v1/datasets")
    wrong_ip = await stand.client.get("/export/v1/datasets", headers={**HEADERS, "X-Real-IP": "192.0.2.1"})
    unknown = await stand.client.get("/export/v1/seller_tax_rates", headers=HEADERS)
    bad_limit = await stand.client.get("/export/v1/orders?limit=0", headers=HEADERS)
    bad_cursor = await stand.client.get("/export/v1/orders?cursor=not-a-cursor", headers=HEADERS)
    configured = stand.app.state.settings
    stand.app.state.settings = replace(
        configured,
        export=replace(configured.export, token="", token_next=""),
    )
    disabled = await stand.client.get("/export/v1/datasets", headers=HEADERS)
    stand.app.state.settings = configured

    assert missing.status_code == 401 and missing.json()["error"]["code"] == "unauthorized"
    assert wrong_ip.status_code == 403 and wrong_ip.json()["error"]["code"] == "unauthorized"
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_dataset"
    assert bad_limit.status_code == 400 and bad_limit.json()["error"]["code"] == "bad_request"
    assert bad_cursor.status_code == 400 and bad_cursor.json()["error"]["code"] == "bad_cursor"
    assert disabled.status_code == 503 and disabled.json()["error"]["code"] == "auth_not_configured"


async def test_every_dataset_matches_the_export_v1_contract(stand: Stand) -> None:
    pages = {}
    for name in (
        "sellers",
        "products",
        "orders",
        "sales_rows",
        "prices",
        "ad_campaigns",
        "ad_stats_daily",
        "stocks",
        "ratings",
    ):
        response = await stand.client.get(f"/export/v1/{name}?limit=5000", headers=HEADERS)
        assert response.status_code == 200, (name, response.text)
        body = response.json()
        assert body["contract"] == "export_v1" and body["dataset"] == name and body["schema_version"] == 1
        assert body["as_of"].endswith("Z")
        pages[name] = [item for item in body["items"] if item["seller_id"] == str(SELLER_ID)]
        assert pages[name], name
        assert all(item["channel"] == "wb" and item["updated_at"].endswith("Z") for item in pages[name])

    assert pages["sellers"][0]["seller_id"] == str(SELLER_ID)
    product = next(item for item in pages["products"] if item["nm_id"] == 1001)
    assert product["barcodes"] == ["460001", "460002", "460003"]
    assert product["title"] == "Карточка один"
    assert pages["orders"][0]["price_kop"] == 12_345
    sale = pages["sales_rows"][0]
    assert (sale["retail_amount_kop"], sale["spp_bp"], sale["commission_bp"]) == (12_345, 1500, 2300)
    assert pages["prices"][0]["club_price_kop"] == 166_250
    assert pages["ad_stats_daily"][0]["placement"] == "all"
    assert pages["ad_stats_daily"][0]["spend_kop"] == 4210
    stocks = pages["stocks"]
    assert {(item["kind"], item["quantity"]) for item in stocks} == {("fbo", 17), ("fbs", 8)}
    assert all(item["warehouse_name"] != "all" for item in stocks)
    assert pages["ratings"][0]["rating_x100"] == 483


async def test_cursor_pages_are_deterministic_idempotent_and_bound_to_the_dataset(stand: Stand) -> None:
    first = await stand.client.get("/export/v1/products?limit=1", headers=HEADERS)
    repeated_first = await stand.client.get("/export/v1/products?limit=1", headers=HEADERS)
    assert first.json()["items"] == repeated_first.json()["items"]
    assert first.json()["next_cursor"] == repeated_first.json()["next_cursor"]
    assert first.json()["has_more"] is True

    cursor = first.json()["next_cursor"]
    second = await stand.client.get("/export/v1/products", params={"limit": 1, "cursor": cursor}, headers=HEADERS)
    repeated_second = await stand.client.get(
        "/export/v1/products", params={"limit": 1, "cursor": cursor}, headers=HEADERS
    )
    assert second.json()["items"] == repeated_second.json()["items"]
    assert second.json()["next_cursor"] == repeated_second.json()["next_cursor"]
    wrong_dataset = await stand.client.get("/export/v1/orders", params={"cursor": cursor}, headers=HEADERS)
    assert wrong_dataset.status_code == 400 and wrong_dataset.json()["error"]["code"] == "bad_cursor"


async def test_empty_page_keeps_the_incoming_cursor(stand: Stand) -> None:
    page = await stand.client.get("/export/v1/products?limit=5000", headers=HEADERS)
    cursor = page.json()["next_cursor"]
    empty = await stand.client.get("/export/v1/products", params={"cursor": cursor}, headers=HEADERS)
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["next_cursor"] == cursor
    assert empty.json()["has_more"] is False


async def test_database_failures_use_the_stable_service_unavailable_error(
    stand: Stand, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(*_args, **_kwargs):
        raise SQLAlchemyError("database is unavailable")

    monkeypatch.setattr(ExportRepository, "page", unavailable)
    response = await stand.client.get("/export/v1/orders", headers=HEADERS)

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "db_unavailable", "message": "export database is unavailable"}}
