from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from backend.infrastructure.export_v1.repository import ExportRow
from backend.infrastructure.observability import ExportBatchObservation, ExportObservability


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


__all__ = ["record_export_page"]
