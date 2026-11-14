import copy
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.exc import SQLAlchemyError

from backend.app.factory import Application
from backend.infrastructure.export_v1.repository import DATASETS, ExportRepository, ExportRow
from backend.infrastructure.observability import ExportObservability
from backend.infrastructure.observability.repository import production_reports_table
from backend.modules.export_v1.observability import record_export_page
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
REPORT_TOKEN = "test-report-token-0123456789abcdef"
REPORT_HEADERS = {"Authorization": f"Bearer {REPORT_TOKEN}"}
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
            await session.execute(delete(production_reports_table))
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
                    await session.execute(delete(production_reports_table))
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
    metrics = await stand.client.get("/export/v1/observability/metrics", headers=HEADERS)

    assert missing.status_code == 401 and missing.json()["error"]["code"] == "unauthorized"
    assert wrong_ip.status_code == 403 and wrong_ip.json()["error"]["code"] == "unauthorized"
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_dataset"
    assert bad_limit.status_code == 400 and bad_limit.json()["error"]["code"] == "bad_request"
    assert bad_cursor.status_code == 400 and bad_cursor.json()["error"]["code"] == "bad_cursor"
    assert disabled.status_code == 503 and disabled.json()["error"]["code"] == "auth_not_configured"
    assert (
        'marketplace_auto_export_v1_http_requests_total{channel="wb",dataset="orders",'
        'status_class="4xx",status_code="400"} 2.0'
    ) in metrics.text


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


async def test_sales_rows_use_numeric_primary_key_order_for_the_indexed_cursor(stand: Stand) -> None:
    async with stand.application.database.session() as session:
        session.add_all(
            [
                SalesReportRowModel(
                    seller_id=SELLER_ID,
                    rrd_id=rrd_id,
                    report_id=700_000 + rrd_id,
                    nm_id=1001,
                    collected_at=STAMP,
                )
                for rrd_id in (10, 2)
            ]
        )
        await session.commit()

    response = await stand.client.get("/export/v1/sales_rows?limit=5000", headers=HEADERS)

    assert response.status_code == 200
    seller_rows = [item["rrd_id"] for item in response.json()["items"] if item["seller_id"] == str(SELLER_ID)]
    assert seller_rows == [2, 10, 900001]


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


async def test_observability_endpoint_uses_export_guard_and_records_real_page_boundary(stand: Stand) -> None:
    page = await stand.client.get("/export/v1/products?limit=1", headers=HEADERS)
    denied = await stand.client.get("/export/v1/observability/metrics")
    metrics = await stand.client.get("/export/v1/observability/metrics", headers=HEADERS)
    status = await stand.client.get("/export/v1/observability/status", headers=HEADERS)

    assert page.status_code == 200
    assert denied.status_code == 401
    assert metrics.status_code == 200
    assert status.status_code == 503  # Closed until the bridge preflight is clean.
    assert 'marketplace_auto_export_v1_rows_total{channel="wb",dataset="products"' in metrics.text
    assert f'seller="{SELLER_ID}",stage="sent"' in metrics.text
    assert "marketplace_auto_export_v1_bytes_total" in metrics.text
    assert "marketplace_auto_export_v1_pages_total" in metrics.text
    assert "marketplace_auto_export_v1_http_requests_total" in metrics.text
    assert page.json()["next_cursor"] not in metrics.text
    assert "seller_tax_rates" not in metrics.text


def test_mixed_seller_page_is_split_into_per_seller_metrics() -> None:
    other_seller = uuid.UUID("a1e048f7-bf93-4e91-9f5a-c2b844fed0c4")
    rows = [
        ExportRow(STAMP, "1", {"seller_id": str(SELLER_ID), "channel": "wb", "value": "first"}),
        ExportRow(STAMP, "2", {"seller_id": str(SELLER_ID), "channel": "wb", "value": "second"}),
        ExportRow(STAMP, "3", {"seller_id": str(other_seller), "channel": "wb", "value": "third"}),
    ]
    metrics = ExportObservability()

    record_export_page(
        metrics,
        dataset="products",
        rows=rows,
        next_cursor="opaque-cursor",
        latency_seconds=0.1,
        transfer_id=uuid.UUID("e8183773-347b-45ac-82a0-0900bd05b78d"),
    )

    snapshot = {item["seller_id"]: item for item in metrics.snapshot()}
    assert snapshot[str(SELLER_ID)]["rows"] == {"sent": 2, "accepted": 0, "written": 0}
    assert snapshot[str(other_seller)]["rows"] == {"sent": 1, "accepted": 0, "written": 0}
    assert snapshot[str(SELLER_ID)]["pages"] == {"sent": 1, "accepted": 0, "written": 0}
    assert "opaque-cursor" not in metrics.render_prometheus().decode()


async def production_report_payload(
    stand: Stand,
    *,
    source: str,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    stamp = observed_at or datetime.now(UTC)
    async with stand.application.database.session() as session:
        counts = await ExportRepository(session).volume_counts()
    stage = {"ma_bridge": "accepted", "mp_sync": "written"}[source]
    return {
        "contract": "export_v1_observability_v1",
        "report_id": str(uuid.uuid4()),
        "observed_at": stamp.isoformat(),
        "channel": "wb",
        "source": source,
        "stage": stage,
        "datasets": [
            {
                "dataset": dataset,
                "rows": counts[dataset],
                "real_sellers": 1 if source == "ma_bridge" else 0,
                "mock_sellers": 0,
                "mock_rows": 0,
                "rejected_rows": 0,
                "unmapped_sellers": 0,
                "backlog_rows": 0,
            }
            for dataset in DATASETS
        ],
    }


async def test_production_report_is_guarded_durable_and_restores_gate_after_restart(stand: Stand) -> None:
    accepted_payload = await production_report_payload(stand, source="ma_bridge")
    written_payload = await production_report_payload(stand, source="mp_sync")
    denied = await stand.client.post("/export/v1/observability/production-report", json=accepted_payload)
    wrong_credential = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=HEADERS,
        json=accepted_payload,
    )
    accepted = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=accepted_payload,
    )
    written = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=written_payload,
    )
    replayed = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=written_payload,
    )
    changed = copy.deepcopy(written_payload)
    changed["datasets"][0]["rows"] = 99  # type: ignore[index]
    conflicting = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=changed,
    )

    assert denied.status_code == 401
    assert wrong_credential.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["consumer_gate_open"] is False
    assert written.status_code == 200
    assert replayed.status_code == 200
    assert conflicting.status_code == 409
    assert written.json()["consumer_gate_open"] is True

    # Simulate a fresh worker: status must rebuild the gate from PostgreSQL.
    stand.app.state.export_observability = ExportObservability()
    status = await stand.client.get("/export/v1/observability/status", headers=HEADERS)
    metrics = await stand.client.get("/export/v1/observability/metrics", headers=HEADERS)

    assert status.status_code == 200
    assert status.json()["ok"] is True
    assert (
        'marketplace_auto_export_v1_production_stage_rows{channel="wb",dataset="orders",stage="written"}'
        in metrics.text
    )


async def test_production_report_rejects_stale_and_non_contract_datasets(stand: Stand) -> None:
    current = await production_report_payload(stand, source="ma_bridge")
    accepted = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=current,
    )
    stale = await production_report_payload(
        stand,
        source="ma_bridge",
        observed_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    rejected = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=stale,
    )
    invalid = await production_report_payload(stand, source="mp_sync")
    invalid["datasets"][0]["dataset"] = "seller_tax_rates"  # type: ignore[index]
    bad_contract = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=invalid,
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "stale_production_report"
    assert bad_contract.status_code == 422


async def test_production_report_rejects_source_stage_spoofing(stand: Stand) -> None:
    payload = await production_report_payload(stand, source="mp_sync")
    payload["stage"] = "accepted"
    response = await stand.client.post(
        "/export/v1/observability/production-report",
        headers=REPORT_HEADERS,
        json=payload,
    )

    assert response.status_code == 422
