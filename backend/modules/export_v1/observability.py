from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.infrastructure.export_v1.repository import ExportRow
from backend.infrastructure.observability import (
    ACTIVE_EXPORT_DATASETS,
    ExportBatchObservation,
    ExportObservability,
    ProductionStageReport,
)


class ProductionDatasetReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str
    rows: int = Field(ge=0)
    real_sellers: int = Field(ge=0)
    mock_sellers: int = Field(ge=0)
    mock_rows: int = Field(ge=0)
    rejected_rows: int = Field(default=0, ge=0)
    unmapped_sellers: int = Field(default=0, ge=0)
    backlog_rows: int = Field(default=0, ge=0)


class ProductionReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract: Literal["export_v1_observability_v1"]
    report_id: uuid.UUID
    observed_at: datetime
    channel: Literal["wb"]
    source: Literal["ma_bridge", "mp_sync"]
    stage: Literal["accepted", "written"]
    datasets: list[ProductionDatasetReport] = Field(min_length=9, max_length=9)

    @field_validator("observed_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("observed_at cannot be in the future")
        return normalized

    @model_validator(mode="after")
    def exact_dataset_contract(self) -> Self:
        names = [item.dataset for item in self.datasets]
        if len(names) != len(set(names)):
            raise ValueError("datasets must not contain duplicates")
        if set(names) != ACTIVE_EXPORT_DATASETS:
            raise ValueError("report must contain exactly the nine export_v1 datasets")
        expected = {"ma_bridge": "accepted", "mp_sync": "written"}[self.source]
        if self.stage != expected:
            raise ValueError("ma_bridge owns accepted and mp_sync owns written")
        if self.source == "mp_sync" and any(
            item.real_sellers or item.mock_sellers or item.mock_rows for item in self.datasets
        ):
            raise ValueError("mp_sync cannot attest bridge seller or mock counts")
        return self

    def to_domain(self, sent_counts: dict[str, int] | None = None) -> list[ProductionStageReport]:
        if self.source == "ma_bridge" and sent_counts is None:
            raise ValueError("accepted reports require exporter counts")
        return [
            ProductionStageReport(
                dataset=item.dataset,
                channel=self.channel,
                source=self.source,
                stage=self.stage,
                rows=item.rows,
                real_sellers=item.real_sellers,
                mock_sellers=item.mock_sellers,
                mock_rows=item.mock_rows,
                rejected_rows=item.rejected_rows,
                unmapped_sellers=item.unmapped_sellers,
                backlog_rows=item.backlog_rows,
                sent_rows=sent_counts[item.dataset] if sent_counts is not None else None,
                observed_at=self.observed_at,
                report_id=self.report_id,
            )
            for item in self.datasets
        ]


@dataclass(slots=True)
class _SellerPage:
    rows: int = 0
    payload_bytes: int = 0
    source_updated_at: datetime | None = None


def record_export_page(
    observer: ExportObservability,
    *,
    dataset: str,
    rows: list[ExportRow],
    next_cursor: str | None,
    latency_seconds: float,
    transfer_id: uuid.UUID | str,
) -> None:
    """Map a mixed-seller export page to bounded per-seller sent metrics."""
    observer.record_http_request(
        dataset=dataset,
        channel="wb",
        status_code=200,
        latency_seconds=latency_seconds,
        transfer_id=transfer_id,
    )
    grouped: dict[tuple[str, str], _SellerPage] = {}
    for row in rows:
        seller_id = str(uuid.UUID(str(row.item["seller_id"])))
        channel = str(row.item["channel"])
        key = (seller_id, channel)
        page = grouped.setdefault(key, _SellerPage())
        page.rows += 1
        page.payload_bytes += len(json.dumps(row.item, ensure_ascii=False, separators=(",", ":")).encode())
        if page.source_updated_at is None or row.updated_at > page.source_updated_at:
            page.source_updated_at = row.updated_at

    for (seller_id, channel), page in grouped.items():
        observer.observe(
            ExportBatchObservation(
                dataset=dataset,
                seller_id=seller_id,
                channel=channel,
                stage="sent",
                rows=page.rows,
                payload_bytes=page.payload_bytes,
                pages=1,
                status_code=200,
                latency_seconds=latency_seconds,
                source_updated_at=page.source_updated_at,
                cursor=next_cursor,
                transfer_id=transfer_id,
            )
        )


__all__ = ["ProductionReportRequest", "record_export_page"]
