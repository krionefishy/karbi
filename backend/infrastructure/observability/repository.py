from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, select, text
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.infrastructure.observability.export_v1 import (
    ACTIVE_EXPORT_DATASETS,
    ProductionStageReport,
    ProductionVolumeReport,
)

PRODUCTION_SOURCES = {"ma_bridge": "accepted", "mp_sync": "written"}

metadata = MetaData()
production_reports_table = Table(
    "export_v1_production_reports",
    metadata,
    Column("dataset", String(32), primary_key=True),
    Column("channel", String(16), primary_key=True),
    Column("source", String(16), primary_key=True),
    Column("stage", String(16), nullable=False),
    Column("report_id", UUID(as_uuid=True), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("rows", BigInteger, nullable=False),
    Column("sent_rows", BigInteger),
    Column("real_sellers", BigInteger, nullable=False),
    Column("mock_sellers", BigInteger, nullable=False),
    Column("mock_rows", BigInteger, nullable=False),
    Column("rejected_rows", BigInteger, nullable=False),
    Column("unmapped_sellers", BigInteger, nullable=False),
    Column("backlog_rows", BigInteger, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    schema="platform",
)


class StaleProductionReport(ValueError):
    pass


class ProductionReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace(self, reports: Sequence[ProductionStageReport]) -> None:
        if not reports:
            raise ValueError("production report cannot be empty")
        first = reports[0]
        if {report.dataset for report in reports} != ACTIVE_EXPORT_DATASETS:
            raise ValueError("production report must contain exactly the active datasets")
        expected_stage = PRODUCTION_SOURCES.get(first.source)
        if expected_stage is None or first.stage != expected_stage:
            raise ValueError("production report source does not own this stage")
        if any(
            report.channel != first.channel
            or report.source != first.source
            or report.stage != first.stage
            or report.report_id != first.report_id
            or report.observed_at != first.observed_at
            for report in reports
        ):
            raise ValueError("production report generation must be internally consistent")
        if first.source == "ma_bridge" and any(report.sent_rows is None for report in reports):
            raise ValueError("accepted reports require exporter sent counts")
        if first.source == "mp_sync" and any(report.sent_rows is not None for report in reports):
            raise ValueError("written reports cannot supply exporter sent counts")

        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"export_v1_production_report:{first.channel}:{first.source}"},
        )
        current = (
            await self.session.execute(
                select(production_reports_table.c.report_id, production_reports_table.c.observed_at)
                .where(
                    production_reports_table.c.channel == first.channel,
                    production_reports_table.c.source == first.source,
                )
                .limit(1)
                .with_for_update()
            )
        ).first()
        if current is not None:
            current_id, current_observed_at = current
            if current_id == first.report_id:
                if set(await self.list_stage(first.channel, first.source)) != set(reports):
                    raise StaleProductionReport("report_id cannot be reused with different counts")
                return
            if first.observed_at <= current_observed_at:
                raise StaleProductionReport("production report is older than the stored generation")

        for report in reports:
            values = {
                "dataset": report.dataset,
                "channel": report.channel,
                "source": report.source,
                "stage": report.stage,
                "report_id": report.report_id,
                "observed_at": report.observed_at,
                "rows": report.rows,
                "sent_rows": report.sent_rows,
                "real_sellers": report.real_sellers,
                "mock_sellers": report.mock_sellers,
                "mock_rows": report.mock_rows,
                "rejected_rows": report.rejected_rows,
                "unmapped_sellers": report.unmapped_sellers,
                "backlog_rows": report.backlog_rows,
                "received_at": datetime.now(UTC),
            }
            statement = insert(production_reports_table).values(**values)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["dataset", "channel", "source"],
                    set_={key: value for key, value in values.items() if key not in {"dataset", "channel", "source"}},
                )
            )

    async def list_stage(self, channel: str, source: str) -> list[ProductionStageReport]:
        rows = (
            await self.session.execute(
                select(production_reports_table)
                .where(
                    production_reports_table.c.channel == channel,
                    production_reports_table.c.source == source,
                )
                .order_by(production_reports_table.c.dataset)
            )
        ).mappings()
        return [self._stage(row) for row in rows]

    async def list(self, channel: str) -> list[ProductionVolumeReport]:
        """Merge independently authenticated accepted and written snapshots."""
        stages = {
            source: {item.dataset: item for item in await self.list_stage(channel, source)}
            for source in PRODUCTION_SOURCES
        }
        output: list[ProductionVolumeReport] = []
        for dataset in sorted(ACTIVE_EXPORT_DATASETS):
            accepted = stages["ma_bridge"].get(dataset)
            written = stages["mp_sync"].get(dataset)
            if accepted is None or written is None or accepted.sent_rows is None:
                continue
            output.append(
                ProductionVolumeReport(
                    dataset=dataset,
                    channel=channel,
                    real_sellers=accepted.real_sellers,
                    mock_sellers=accepted.mock_sellers,
                    mock_rows=accepted.mock_rows,
                    sent_rows=accepted.sent_rows,
                    accepted_rows=accepted.rows,
                    written_rows=written.rows,
                    rejected_rows=accepted.rejected_rows + written.rejected_rows,
                    unmapped_sellers=accepted.unmapped_sellers + written.unmapped_sellers,
                    backlog_rows=accepted.backlog_rows + written.backlog_rows,
                    observed_at=min(accepted.observed_at, written.observed_at),
                    report_id=accepted.report_id,
                )
            )
        return output

    @staticmethod
    def _stage(row) -> ProductionStageReport:
        return ProductionStageReport(
            dataset=row["dataset"],
            channel=row["channel"],
            source=row["source"],
            stage=row["stage"],
            rows=row["rows"],
            sent_rows=row["sent_rows"],
            real_sellers=row["real_sellers"],
            mock_sellers=row["mock_sellers"],
            mock_rows=row["mock_rows"],
            rejected_rows=row["rejected_rows"],
            unmapped_sellers=row["unmapped_sellers"],
            backlog_rows=row["backlog_rows"],
            observed_at=row["observed_at"],
            report_id=uuid.UUID(str(row["report_id"])),
        )


__all__ = ["ProductionReportRepository", "StaleProductionReport", "production_reports_table"]
