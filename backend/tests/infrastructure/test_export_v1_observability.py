import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import APIRouter, FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST

from backend.infrastructure.observability import (
    ACTIVE_EXPORT_DATASETS,
    ExportBatchObservation,
    ExportHealthThresholds,
    ExportObservability,
    ProductionVolumeReport,
    create_export_observability_router,
)

SELLER = uuid.UUID("d17336d1-6ff8-4b56-9a73-f082e6e2ff98")
NOW = datetime(2026, 11, 14, 0, 0, tzinfo=UTC)


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def info(self, event: str, **values: object) -> None:
        self.events.append({"event": event, **values})

    def warning(self, event: str, **values: object) -> None:
        self.events.append({"event": event, **values})


def observation(stage: str = "sent", **overrides: object) -> ExportBatchObservation:
    values: dict[str, object] = {
        "dataset": "sales_rows",
        "seller_id": SELLER,
        "channel": "wb",
        "stage": stage,
        "rows": 10,
        "payload_bytes": 2048,
        "pages": 1,
        "status_code": 200,
        "latency_seconds": 0.25,
        "backlog_rows": 7,
        "source_updated_at": NOW - timedelta(minutes=2),
        "cursor": "opaque:cursor:with-business-key",
        "transfer_id": uuid.UUID("cc71a7c3-cf58-4459-95ac-1443b00ba3a8"),
        "observed_at": NOW,
    }
    values.update(overrides)
    return ExportBatchObservation(**values)  # type: ignore[arg-type]


def test_active_datasets_are_exactly_the_nine_export_v1_sets() -> None:
    assert {
        "sellers",
        "products",
        "orders",
        "sales_rows",
        "prices",
        "ad_campaigns",
        "ad_stats_daily",
        "stocks",
        "ratings",
    } == ACTIVE_EXPORT_DATASETS


def test_batch_metrics_cover_counts_status_lag_latency_cursor_and_backlog() -> None:
    metrics = ExportObservability()
    metrics.observe(observation(retries=2, retry_reason="rate_limited", rejected_rows=1, rejection_reason="validation"))

    rendered = metrics.render_prometheus().decode()
    assert 'marketplace_auto_export_v1_rows_total{channel="wb",dataset="sales_rows"' in rendered
    assert 'seller="d17336d1-6ff8-4b56-9a73-f082e6e2ff98",stage="sent"} 10.0' in rendered
    assert 'marketplace_auto_export_v1_bytes_total{channel="wb",dataset="sales_rows"' in rendered
    assert 'marketplace_auto_export_v1_pages_total{channel="wb",dataset="sales_rows"' in rendered
    assert 'status_class="2xx",status_code="200"' in rendered
    assert 'marketplace_auto_export_v1_retries_total{channel="wb",dataset="sales_rows"' in rendered
    assert 'reason="rate_limited"' in rendered
    assert 'marketplace_auto_export_v1_rejected_rows_total{channel="wb",dataset="sales_rows"' in rendered
    assert "marketplace_auto_export_v1_source_lag_seconds" in rendered
    assert " 120.0\n" in rendered
    assert "marketplace_auto_export_v1_backlog_rows" in rendered
    assert "marketplace_auto_export_v1_cursor_advances_total" in rendered
    assert "marketplace_auto_export_v1_latency_seconds_bucket" in rendered


def test_429_and_5xx_have_distinct_bounded_status_classes() -> None:
    metrics = ExportObservability()
    metrics.observe(observation(status_code=429, rows=0, payload_bytes=0, error_code="rate_limited"))
    metrics.observe(observation(status_code=503, rows=0, payload_bytes=0, error_code="upstream_unavailable"))

    rendered = metrics.render_prometheus().decode()
    assert 'status_class="429",status_code="429"' in rendered
    assert 'status_class="5xx",status_code="503"' in rendered


def test_reconciliation_tracks_sent_accepted_written_without_payloads() -> None:
    metrics = ExportObservability()
    metrics.observe(observation("sent", rows=10))
    metrics.observe(observation("accepted", rows=9, rejected_rows=1, rejection_reason="schema"))
    metrics.observe(observation("written", rows=8))

    [snapshot] = metrics.snapshot()
    assert snapshot["rows"] == {"sent": 10, "accepted": 9, "written": 8}
    assert snapshot["sent_accepted_gap"] == 1
    assert snapshot["accepted_written_gap"] == 1
    rendered = metrics.render_prometheus().decode()
    assert 'from_stage="sent"' in rendered and 'to_stage="accepted"' in rendered
    assert 'from_stage="accepted"' in rendered and 'to_stage="written"' in rendered


def test_cursor_and_secrets_are_fingerprinted_or_rejected_not_logged_or_labelled() -> None:
    log = RecordingLogger()
    metrics = ExportObservability(logger=log)
    cursor = "Bearer top-secret-token:cursor:123"
    metrics.observe(observation(cursor=cursor))

    rendered = metrics.render_prometheus().decode()
    assert cursor not in rendered
    assert cursor not in str(log.events)
    assert log.events[0]["cursor_fingerprint"] and len(str(log.events[0]["cursor_fingerprint"])) == 16
    with pytest.raises(ValueError, match="machine code"):
        metrics.observe(observation(error_code="Authorization: Bearer leaked-token"))


def test_deprecated_or_unknown_dataset_cannot_refresh_freshness() -> None:
    metrics = ExportObservability()
    with pytest.raises(ValueError, match="unsupported dataset"):
        metrics.observe(observation(dataset="seller_tax_rates"))
    assert metrics.snapshot() == []
    assert "seller_tax_rates" not in metrics.render_prometheus().decode()


def test_unmapped_seller_is_critical_without_uuid_metric_cardinality() -> None:
    log = RecordingLogger()
    metrics = ExportObservability(logger=log)
    metrics.record_unmapped_seller(dataset="orders", channel="wb", seller_id=SELLER)
    metrics.set_unmapped_sellers(dataset="orders", channel="wb", count=1)

    rendered = metrics.render_prometheus().decode()
    assert "marketplace_auto_export_v1_unmapped_seller_events_total" in rendered
    assert "marketplace_auto_export_v1_unmapped_sellers" in rendered
    assert str(SELLER) not in rendered
    assert str(SELLER) not in str(log.events)
    assert log.events[0]["seller_fingerprint"]
    status = metrics.status(now=NOW)
    assert not status.ok
    assert [(issue.code, issue.seller_id, issue.value, issue.stage) for issue in status.issues] == [
        ("unmapped_seller", None, 1.0, None)
    ]


def test_health_detects_stall_lag_backlog_and_counter_gaps() -> None:
    metrics = ExportObservability()
    metrics.observe(observation(rows=10, backlog_rows=100, source_updated_at=NOW - timedelta(hours=1)))
    metrics.observe(
        observation(
            "accepted",
            rows=0,
            payload_bytes=0,
            backlog_rows=100,
            source_updated_at=NOW - timedelta(hours=1),
        )
    )

    status = metrics.status(
        ExportHealthThresholds(
            stalled_after_seconds=60,
            source_lag_seconds=60,
            backlog_rows=10,
            reconciliation_gap_rows=0,
        ),
        now=NOW + timedelta(minutes=2),
    )
    assert not status.ok
    assert {issue.code for issue in status.issues} == {"stalled", "source_lag", "backlog", "gap_sent_accepted"}


def test_production_consumer_gate_requires_clean_volume_for_all_nine_datasets() -> None:
    metrics = ExportObservability()
    for dataset in ACTIVE_EXPORT_DATASETS:
        assert metrics.report_production_volume(
            ProductionVolumeReport(
                dataset=dataset,
                channel="wb",
                real_sellers=2,
                mock_sellers=0,
                mock_rows=0,
                sent_rows=100,
                bridge_rows=100,
            )
        )
    assert metrics.production_gate_status(channel="wb", now=NOW).ok
    rendered = metrics.render_prometheus().decode()
    assert "marketplace_auto_export_v1_production_real_sellers" in rendered
    assert "marketplace_auto_export_v1_production_consumer_gate_open" in rendered


def test_production_consumer_gate_blocks_mock_data_missing_datasets_and_bridge_gap() -> None:
    metrics = ExportObservability()
    assert not metrics.report_production_volume(
        ProductionVolumeReport(
            dataset="orders",
            channel="wb",
            real_sellers=0,
            mock_sellers=1,
            mock_rows=20,
            sent_rows=100,
            bridge_rows=90,
        )
    )

    status = metrics.production_gate_status(channel="wb", now=NOW)
    assert not status.ok
    codes = {issue.code for issue in status.issues if issue.dataset == "orders"}
    assert codes == {"no_real_sellers", "mock_sellers", "mock_rows", "gap_sent_bridge"}
    assert sum(issue.code == "production_volume_missing" for issue in status.issues) == 8


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"seller_id": "not-a-uuid"}, "seller_id"),
        ({"channel": "marketplace-auto"}, "channel"),
        ({"stage": "parsed"}, "stage"),
        ({"rows": -1}, "rows"),
        ({"pages": 0}, "pages"),
        ({"latency_seconds": float("nan")}, "latency_seconds"),
        ({"status_code": 700}, "status_code"),
    ],
)
def test_cardinality_and_numeric_inputs_are_bounded(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ExportObservability().observe(observation(**overrides))  # type: ignore[arg-type]


async def test_endpoint_factory_requires_callers_machine_auth_and_never_exposes_cursor() -> None:
    metrics = ExportObservability()
    metrics.observe(observation())

    async def authorize(authorization: str = Header(default="")) -> None:
        if authorization != "Bearer machine-test-token":
            raise HTTPException(status_code=401)

    app = FastAPI()
    parent = APIRouter(prefix="/export/v1")
    parent.include_router(create_export_observability_router(metrics, authorize=authorize))
    app.include_router(parent)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get("/export/v1/observability/metrics")
        allowed = await client.get(
            "/export/v1/observability/metrics",
            headers={"Authorization": "Bearer machine-test-token"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "opaque:cursor:with-business-key" not in allowed.text


async def test_status_endpoint_stays_closed_until_production_preflight_is_complete() -> None:
    metrics = ExportObservability()

    async def authorize() -> None:
        return None

    app = FastAPI()
    parent = APIRouter(prefix="/export/v1")
    parent.include_router(create_export_observability_router(metrics, authorize=authorize))
    app.include_router(parent)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/export/v1/observability/status")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert len(response.json()["production_gate_issues"]) == 9
