"""Bounded, payload-free observability for the export_v1 transfer chain.

The module deliberately does not know how an export page is serialized.  Callers
report counts and timings after the page has crossed one of the three boundaries.
That keeps rows, cursors and authorization headers out of both metrics and logs.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

ACTIVE_EXPORT_DATASETS = frozenset(
    {
        "sellers",
        "products",
        "orders",
        "sales_rows",
        "prices",
        "ad_campaigns",
        "ad_stats_daily",
        "stocks",
        "ratings",
    }
)
EXPORT_CHANNELS = frozenset({"wb", "ozon"})
EXPORT_STAGES = ("sent", "accepted", "written")
RETRY_REASONS = frozenset({"rate_limited", "server_error", "network", "other"})
REJECTION_REASONS = frozenset({"validation", "unmapped_seller", "schema", "other"})

_SAFE_CODE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,63}")
_HISTOGRAM_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


@dataclass(frozen=True, slots=True)
class ExportBatchObservation:
    dataset: str
    seller_id: uuid.UUID | str
    channel: str
    stage: str
    rows: int
    payload_bytes: int
    pages: int = 1
    status_code: int = 200
    latency_seconds: float = 0.0
    retries: int = 0
    retry_reason: str = "other"
    rejected_rows: int = 0
    rejection_reason: str = "other"
    backlog_rows: int | None = None
    source_updated_at: datetime | None = None
    cursor: str | bytes | None = field(default=None, repr=False)
    transfer_id: uuid.UUID | str | None = None
    error_code: str | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExportHealthThresholds:
    stalled_after_seconds: float = 15 * 60
    source_lag_seconds: float = 30 * 60
    backlog_rows: int = 50_000
    reconciliation_gap_rows: int = 0


@dataclass(frozen=True, slots=True)
class ExportIssue:
    code: str
    dataset: str
    seller_id: str | None
    channel: str
    value: float
    stage: str | None = None


@dataclass(frozen=True, slots=True)
class ExportStatus:
    ok: bool
    checked_at: datetime
    issues: tuple[ExportIssue, ...]


@dataclass(frozen=True, slots=True)
class ProductionVolumeReport:
    """Aggregate counts from a production preflight; never accepts row payloads."""

    dataset: str
    channel: str
    real_sellers: int
    mock_sellers: int
    mock_rows: int
    sent_rows: int
    bridge_rows: int


@dataclass(slots=True)
class _SeriesState:
    rows: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXPORT_STAGES, 0))
    payload_bytes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXPORT_STAGES, 0))
    pages: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXPORT_STAGES, 0))
    last_success_at: dict[str, float] = field(default_factory=dict)
    source_lag_seconds: dict[str, float] = field(default_factory=dict)
    backlog_rows: dict[str, int] = field(default_factory=dict)
    cursor_fingerprint: dict[str, str] = field(default_factory=dict)
    cursor_updates: dict[str, int] = field(default_factory=dict)
    observed_stages: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _ValidatedObservation:
    labels: tuple[str, str, str, str]
    observed_at: datetime
    transfer_id: str


class ExportObservability:
    """Prometheus metrics, safe structured logs and local reconciliation state.

    A registry is owned by each instance so tests and embedders never collide on
    process-global metric names.  Production should create one instance per
    process and expose ``render_prometheus`` behind the export machine-token and
    vm-bridge network restriction from KRB-39.
    """

    def __init__(
        self,
        *,
        registry: CollectorRegistry | None = None,
        logger: Any | None = None,
    ) -> None:
        self.registry = registry or CollectorRegistry(auto_describe=True)
        self._logger = logger or structlog.get_logger("export_v1.observability")
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str, str], _SeriesState] = {}
        self._unmapped: dict[tuple[str, str], int] = {}
        self._production: dict[tuple[str, str], ProductionVolumeReport] = {}

        common = ("dataset", "seller", "channel", "stage")
        self._rows = Counter(
            "marketplace_auto_export_v1_rows",
            "Rows reaching a transfer stage.",
            common,
            registry=self.registry,
        )
        self._bytes = Counter(
            "marketplace_auto_export_v1_bytes",
            "Serialized bytes reaching a transfer stage.",
            common,
            registry=self.registry,
        )
        self._pages = Counter(
            "marketplace_auto_export_v1_pages",
            "Pages reaching a transfer stage.",
            common,
            registry=self.registry,
        )
        self._requests = Counter(
            "marketplace_auto_export_v1_requests",
            "Observed transfer requests by bounded HTTP status.",
            (*common, "status_class", "status_code"),
            registry=self.registry,
        )
        self._latency = Histogram(
            "marketplace_auto_export_v1_latency_seconds",
            "Transfer boundary latency.",
            common,
            buckets=_HISTOGRAM_BUCKETS,
            registry=self.registry,
        )
        http_labels = ("dataset", "channel")
        self._http_requests = Counter(
            "marketplace_auto_export_v1_http_requests",
            "Exporter HTTP requests, including failures without a seller context.",
            (*http_labels, "status_class", "status_code"),
            registry=self.registry,
        )
        self._http_latency = Histogram(
            "marketplace_auto_export_v1_http_latency_seconds",
            "Exporter HTTP request latency, including failed requests.",
            http_labels,
            buckets=_HISTOGRAM_BUCKETS,
            registry=self.registry,
        )
        self._retries = Counter(
            "marketplace_auto_export_v1_retries",
            "Retries before a transfer boundary completed.",
            (*common, "reason"),
            registry=self.registry,
        )
        self._rejected = Counter(
            "marketplace_auto_export_v1_rejected_rows",
            "Rows rejected at a transfer boundary.",
            (*common, "reason"),
            registry=self.registry,
        )
        series_labels = ("dataset", "seller", "channel")
        self._last_success = Gauge(
            "marketplace_auto_export_v1_last_success_unixtime_seconds",
            "Unix time of the last successful observation.",
            common,
            registry=self.registry,
        )
        self._source_lag = Gauge(
            "marketplace_auto_export_v1_source_lag_seconds",
            "Age of the newest source row reported by the exporter.",
            common,
            registry=self.registry,
        )
        self._backlog = Gauge(
            "marketplace_auto_export_v1_backlog_rows",
            "Rows waiting after the observed boundary.",
            common,
            registry=self.registry,
        )
        self._cursor_updates = Counter(
            "marketplace_auto_export_v1_cursor_advances",
            "Successful opaque cursor advances; cursor values are never labels.",
            common,
            registry=self.registry,
        )
        self._gap = Gauge(
            "marketplace_auto_export_v1_reconciliation_gap_rows",
            "Cumulative upstream rows minus downstream rows in this process.",
            (*series_labels, "from_stage", "to_stage"),
            registry=self.registry,
        )
        self._unmapped_events = Counter(
            "marketplace_auto_export_v1_unmapped_seller_events",
            "Transfer attempts blocked by an unknown seller mapping.",
            ("dataset", "channel"),
            registry=self.registry,
        )
        self._unmapped_backlog = Gauge(
            "marketplace_auto_export_v1_unmapped_sellers",
            "Current unresolved seller mappings; any positive value is critical.",
            ("dataset", "channel"),
            registry=self.registry,
        )
        production_labels = ("dataset", "channel")
        self._production_real_sellers = Gauge(
            "marketplace_auto_export_v1_production_real_sellers",
            "Distinct real sellers present in the production preflight volume.",
            production_labels,
            registry=self.registry,
        )
        self._production_mock_sellers = Gauge(
            "marketplace_auto_export_v1_production_mock_sellers",
            "Distinct mock sellers detected in the production preflight volume.",
            production_labels,
            registry=self.registry,
        )
        self._production_mock_rows = Gauge(
            "marketplace_auto_export_v1_production_mock_rows",
            "Mock rows detected in the production preflight volume.",
            production_labels,
            registry=self.registry,
        )
        self._production_sent_bridge_gap = Gauge(
            "marketplace_auto_export_v1_production_sent_bridge_gap_rows",
            "Marketplace Auto sent rows minus bridge accepted rows during preflight.",
            production_labels,
            registry=self.registry,
        )
        self._production_gate = Gauge(
            "marketplace_auto_export_v1_production_consumer_gate_open",
            "One only when production volume is real, mock-free and reconciled.",
            production_labels,
            registry=self.registry,
        )

    def observe(self, observation: ExportBatchObservation) -> str:
        values = _validate_observation(observation)
        dataset, seller, channel, stage = values.labels
        labels = (dataset, seller, channel, stage)
        observed_at = values.observed_at

        self._rows.labels(*labels).inc(observation.rows)
        self._bytes.labels(*labels).inc(observation.payload_bytes)
        self._pages.labels(*labels).inc(observation.pages)
        self._requests.labels(
            *labels,
            _status_class(observation.status_code),
            str(observation.status_code),
        ).inc()
        self._latency.labels(*labels).observe(observation.latency_seconds)
        if observation.retries:
            self._retries.labels(*labels, observation.retry_reason).inc(observation.retries)
        if observation.rejected_rows:
            self._rejected.labels(*labels, observation.rejection_reason).inc(observation.rejected_rows)

        key = (dataset, seller, channel)
        cursor_fingerprint = _fingerprint(observation.cursor) if observation.cursor is not None else None
        with self._lock:
            state = self._series.setdefault(key, _SeriesState())
            state.rows[stage] += observation.rows
            state.payload_bytes[stage] += observation.payload_bytes
            state.pages[stage] += observation.pages
            state.observed_stages.add(stage)
            if 200 <= observation.status_code < 300:
                state.last_success_at[stage] = observed_at.timestamp()
            if observation.source_updated_at is not None:
                state.source_lag_seconds[stage] = max(
                    0.0,
                    (observed_at - observation.source_updated_at.astimezone(UTC)).total_seconds(),
                )
            if observation.backlog_rows is not None:
                state.backlog_rows[stage] = observation.backlog_rows
            if cursor_fingerprint is not None and cursor_fingerprint != state.cursor_fingerprint.get(stage):
                state.cursor_fingerprint[stage] = cursor_fingerprint
                state.cursor_updates[stage] = state.cursor_updates.get(stage, 0) + 1
                self._cursor_updates.labels(*labels).inc()
            self._set_series_gauges(key, stage, state)

        transfer_id = values.transfer_id
        log = self._logger.info if 200 <= observation.status_code < 400 else self._logger.warning
        log(
            "export_v1_batch_observed",
            dataset=dataset,
            seller_id=seller,
            channel=channel,
            stage=stage,
            transfer_id=transfer_id,
            rows=observation.rows,
            payload_bytes=observation.payload_bytes,
            pages=observation.pages,
            status_code=observation.status_code,
            latency_seconds=observation.latency_seconds,
            retries=observation.retries,
            rejected_rows=observation.rejected_rows,
            backlog_rows=observation.backlog_rows,
            source_lag_seconds=state.source_lag_seconds.get(stage),
            cursor_fingerprint=cursor_fingerprint,
            error_code=observation.error_code,
        )
        return transfer_id

    def record_http_request(
        self,
        *,
        dataset: str,
        channel: str,
        status_code: int,
        latency_seconds: float,
        transfer_id: uuid.UUID | str | None = None,
        error_code: str | None = None,
    ) -> str:
        """Record one bounded exporter request even when no seller row was returned."""
        dataset = _active_dataset(dataset)
        channel = _member("channel", channel, EXPORT_CHANNELS)
        if not 100 <= status_code <= 599:
            raise ValueError("status_code must be between 100 and 599")
        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            raise ValueError("latency_seconds must be finite and non-negative")
        if error_code is not None and not _SAFE_CODE.fullmatch(error_code):
            raise ValueError("error_code must be a bounded machine code, not an error message")
        transfer = _transfer_id(transfer_id)
        self._http_requests.labels(
            dataset,
            channel,
            _status_class(status_code),
            str(status_code),
        ).inc()
        self._http_latency.labels(dataset, channel).observe(latency_seconds)
        log = self._logger.info if 200 <= status_code < 400 else self._logger.warning
        log(
            "export_v1_http_request_observed",
            dataset=dataset,
            channel=channel,
            transfer_id=transfer,
            status_code=status_code,
            latency_seconds=latency_seconds,
            error_code=error_code,
        )
        return transfer

    def record_unmapped_seller(
        self,
        *,
        dataset: str,
        channel: str,
        seller_id: uuid.UUID | str,
        transfer_id: uuid.UUID | str | None = None,
    ) -> str:
        dataset = _active_dataset(dataset)
        channel = _member("channel", channel, EXPORT_CHANNELS)
        seller = _seller_id(seller_id)
        transfer = _transfer_id(transfer_id)
        self._unmapped_events.labels(dataset, channel).inc()
        self._logger.warning(
            "export_v1_unmapped_seller",
            dataset=dataset,
            channel=channel,
            seller_fingerprint=_fingerprint(seller),
            transfer_id=transfer,
        )
        return transfer

    def set_unmapped_sellers(self, *, dataset: str, channel: str, count: int) -> None:
        dataset = _active_dataset(dataset)
        channel = _member("channel", channel, EXPORT_CHANNELS)
        _non_negative("count", count)
        with self._lock:
            self._unmapped[(dataset, channel)] = count
            self._unmapped_backlog.labels(dataset, channel).set(count)

    def report_production_volume(self, report: ProductionVolumeReport) -> bool:
        dataset = _active_dataset(report.dataset)
        channel = _member("channel", report.channel, EXPORT_CHANNELS)
        for name in ("real_sellers", "mock_sellers", "mock_rows", "sent_rows", "bridge_rows"):
            _non_negative(name, getattr(report, name))
        clean = (
            report.real_sellers > 0
            and report.mock_sellers == 0
            and report.mock_rows == 0
            and report.sent_rows == report.bridge_rows
        )
        labels = (dataset, channel)
        with self._lock:
            self._production[labels] = report
            self._production_real_sellers.labels(*labels).set(report.real_sellers)
            self._production_mock_sellers.labels(*labels).set(report.mock_sellers)
            self._production_mock_rows.labels(*labels).set(report.mock_rows)
            self._production_sent_bridge_gap.labels(*labels).set(report.sent_rows - report.bridge_rows)
            self._production_gate.labels(*labels).set(int(clean))
        self._logger.info(
            "export_v1_production_volume_checked",
            dataset=dataset,
            channel=channel,
            real_sellers=report.real_sellers,
            mock_sellers=report.mock_sellers,
            mock_rows=report.mock_rows,
            sent_rows=report.sent_rows,
            bridge_rows=report.bridge_rows,
            consumer_gate_open=clean,
        )
        return clean

    def production_gate_status(
        self,
        *,
        channel: str = "wb",
        now: datetime | None = None,
    ) -> ExportStatus:
        """Require a clean report for every active dataset before a consumer starts."""
        channel = _member("channel", channel, EXPORT_CHANNELS)
        checked_at = _aware_utc(now or datetime.now(UTC), "now")
        issues: list[ExportIssue] = []
        with self._lock:
            reports = dict(self._production)
        for dataset in sorted(ACTIVE_EXPORT_DATASETS):
            report = reports.get((dataset, channel))
            if report is None:
                issues.append(ExportIssue("production_volume_missing", dataset, None, channel, 1.0))
                continue
            if report.real_sellers == 0:
                issues.append(ExportIssue("no_real_sellers", dataset, None, channel, 0.0))
            if report.mock_sellers:
                issues.append(ExportIssue("mock_sellers", dataset, None, channel, float(report.mock_sellers)))
            if report.mock_rows:
                issues.append(ExportIssue("mock_rows", dataset, None, channel, float(report.mock_rows)))
            gap = report.sent_rows - report.bridge_rows
            if gap:
                issues.append(ExportIssue("gap_sent_bridge", dataset, None, channel, float(gap)))
        return ExportStatus(ok=not issues, checked_at=checked_at, issues=tuple(issues))

    def status(
        self,
        thresholds: ExportHealthThresholds = ExportHealthThresholds(),
        *,
        now: datetime | None = None,
    ) -> ExportStatus:
        checked_at = _aware_utc(now or datetime.now(UTC), "now")
        issues: list[ExportIssue] = []
        with self._lock:
            series = {key: _copy_state(state) for key, state in self._series.items()}
            unmapped = dict(self._unmapped)

        for (dataset, seller, channel), state in sorted(series.items()):
            for stage in sorted(state.observed_stages):
                last_success = state.last_success_at.get(stage)
                if last_success is not None:
                    age = checked_at.timestamp() - last_success
                    if age > thresholds.stalled_after_seconds:
                        issues.append(ExportIssue("stalled", dataset, seller, channel, age, stage))
                source_lag = state.source_lag_seconds.get(stage)
                if source_lag is not None and source_lag > thresholds.source_lag_seconds:
                    issues.append(ExportIssue("source_lag", dataset, seller, channel, source_lag, stage))
                backlog = state.backlog_rows.get(stage)
                if backlog is not None and backlog > thresholds.backlog_rows:
                    issues.append(ExportIssue("backlog", dataset, seller, channel, float(backlog), stage))
            for upstream, downstream in zip(EXPORT_STAGES[:-1], EXPORT_STAGES[1:], strict=True):
                if downstream not in state.observed_stages:
                    continue
                gap = state.rows[upstream] - state.rows[downstream]
                if abs(gap) > thresholds.reconciliation_gap_rows:
                    issues.append(
                        ExportIssue(
                            f"gap_{upstream}_{downstream}",
                            dataset,
                            seller,
                            channel,
                            float(gap),
                            f"{upstream}->{downstream}",
                        )
                    )

        for (dataset, channel), count in sorted(unmapped.items()):
            if count:
                issues.append(ExportIssue("unmapped_seller", dataset, None, channel, float(count)))
        return ExportStatus(ok=not issues, checked_at=checked_at, issues=tuple(issues))

    def snapshot(self) -> list[dict[str, object]]:
        """A payload-free diagnostic view suitable for an authenticated JSON endpoint."""
        with self._lock:
            return [
                {
                    "dataset": dataset,
                    "seller_id": seller,
                    "channel": channel,
                    "rows": dict(state.rows),
                    "payload_bytes": dict(state.payload_bytes),
                    "pages": dict(state.pages),
                    "last_success_at": dict(state.last_success_at),
                    "source_lag_seconds": dict(state.source_lag_seconds),
                    "backlog_rows": dict(state.backlog_rows),
                    "cursor_fingerprint": dict(state.cursor_fingerprint),
                    "cursor_updates": dict(state.cursor_updates),
                    "sent_accepted_gap": state.rows["sent"] - state.rows["accepted"]
                    if "accepted" in state.observed_stages
                    else None,
                    "accepted_written_gap": state.rows["accepted"] - state.rows["written"]
                    if "written" in state.observed_stages
                    else None,
                }
                for (dataset, seller, channel), state in sorted(self._series.items())
            ]

    def render_prometheus(self) -> bytes:
        return generate_latest(self.registry)

    def _set_series_gauges(self, key: tuple[str, str, str], stage: str, state: _SeriesState) -> None:
        labels = (*key, stage)
        if stage in state.last_success_at:
            self._last_success.labels(*labels).set(state.last_success_at[stage])
        if stage in state.source_lag_seconds:
            self._source_lag.labels(*labels).set(state.source_lag_seconds[stage])
        if stage in state.backlog_rows:
            self._backlog.labels(*labels).set(state.backlog_rows[stage])
        for upstream, downstream in zip(EXPORT_STAGES[:-1], EXPORT_STAGES[1:], strict=True):
            if downstream not in state.observed_stages:
                continue
            self._gap.labels(*key, upstream, downstream).set(state.rows[upstream] - state.rows[downstream])


def _validate_observation(observation: ExportBatchObservation) -> _ValidatedObservation:
    dataset = _active_dataset(observation.dataset)
    seller = _seller_id(observation.seller_id)
    channel = _member("channel", observation.channel, EXPORT_CHANNELS)
    stage = _member("stage", observation.stage, frozenset(EXPORT_STAGES))
    for name in ("rows", "payload_bytes", "pages", "retries", "rejected_rows"):
        _non_negative(name, getattr(observation, name))
    if observation.pages < 1:
        raise ValueError("pages must be at least 1")
    if observation.backlog_rows is not None:
        _non_negative("backlog_rows", observation.backlog_rows)
    if not 100 <= observation.status_code <= 599:
        raise ValueError("status_code must be between 100 and 599")
    if not math.isfinite(observation.latency_seconds) or observation.latency_seconds < 0:
        raise ValueError("latency_seconds must be finite and non-negative")
    _member("retry_reason", observation.retry_reason, RETRY_REASONS)
    _member("rejection_reason", observation.rejection_reason, REJECTION_REASONS)
    if observation.error_code is not None and not _SAFE_CODE.fullmatch(observation.error_code):
        raise ValueError("error_code must be a bounded machine code, not an error message")
    if observation.source_updated_at is not None:
        _aware_utc(observation.source_updated_at, "source_updated_at")
    observed_at = _aware_utc(observation.observed_at or datetime.now(UTC), "observed_at")
    return _ValidatedObservation(
        labels=(dataset, seller, channel, stage),
        observed_at=observed_at,
        transfer_id=_transfer_id(observation.transfer_id),
    )


def _active_dataset(value: str) -> str:
    return _member("dataset", value, ACTIVE_EXPORT_DATASETS)


def _member(name: str, value: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise ValueError(f"unsupported {name}: {value!r}")
    return value


def _seller_id(value: uuid.UUID | str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError) as error:
        raise ValueError("seller_id must be a UUID") from error


def _transfer_id(value: uuid.UUID | str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError) as error:
        raise ValueError("transfer_id must be a UUID") from error


def _non_negative(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _fingerprint(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()[:16]


def _status_class(status_code: int) -> str:
    if status_code == 429:
        return "429"
    return f"{status_code // 100}xx"


def _copy_state(state: _SeriesState) -> _SeriesState:
    return _SeriesState(
        rows=dict(state.rows),
        payload_bytes=dict(state.payload_bytes),
        pages=dict(state.pages),
        last_success_at=dict(state.last_success_at),
        source_lag_seconds=dict(state.source_lag_seconds),
        backlog_rows=dict(state.backlog_rows),
        cursor_fingerprint=dict(state.cursor_fingerprint),
        cursor_updates=dict(state.cursor_updates),
        observed_stages=set(state.observed_stages),
    )
