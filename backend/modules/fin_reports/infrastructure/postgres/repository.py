import uuid
from collections.abc import Iterable
from dataclasses import asdict, fields
from datetime import date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.domain import CostPrice
from backend.modules.fin_reports.infrastructure.postgres.models import (
    CostPriceModel,
    TrackedSellerModel,
    WbFactModel,
    WbReportModel,
)
from backend.modules.wb_core.domain import SalesReport, SalesReportTotals

_INSERT_CHUNK = 1000
# Предел asyncpg на число параметров запроса: порция широкой таблицы считается от колонок.
_MAX_QUERY_PARAMETERS = 32_767


class FinReportsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- подключения ------------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(CostPriceModel).where(CostPriceModel.seller_id == seller_id))
        await self.session.execute(delete(WbFactModel).where(WbFactModel.seller_id == seller_id))
        await self.session.execute(delete(WbReportModel).where(WbReportModel.seller_id == seller_id))
        await self.untrack(seller_id)

    # --- себестоимость -----------------------------------------------------------

    async def cost_prices(self, seller_ids: Iterable[uuid.UUID], marketplace: str) -> list[CostPrice]:
        wanted = list(seller_ids)
        if not wanted:
            return []
        rows = await self.session.scalars(
            select(CostPriceModel)
            .where(CostPriceModel.seller_id.in_(wanted), CostPriceModel.marketplace == marketplace)
            .order_by(CostPriceModel.seller_id, CostPriceModel.article, CostPriceModel.effective_from)
        )
        return [
            CostPrice(
                seller_id=row.seller_id,
                marketplace=row.marketplace,
                article=row.article,
                vendor_code=row.vendor_code,
                cost=row.cost,
                effective_from=row.effective_from,
            )
            for row in rows
        ]

    async def save_cost_prices(self, prices: Iterable[CostPrice], *, uploaded_by: uuid.UUID | None) -> None:
        """Версия на ту же дату переписывается: файл, загруженный дважды за день, — одна версия."""
        rows = [
            {
                "seller_id": price.seller_id,
                "marketplace": price.marketplace,
                "article": price.article,
                "effective_from": price.effective_from,
                "vendor_code": price.vendor_code,
                "cost": price.cost,
                "uploaded_by": uploaded_by,
            }
            for price in prices
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            statement = insert(CostPriceModel).values(rows[offset : offset + _INSERT_CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "marketplace", "article", "effective_from"],
                    set_={
                        "vendor_code": excluded.vendor_code,
                        "cost": excluded.cost,
                        "uploaded_by": excluded.uploaded_by,
                        "uploaded_at": func.now(),
                    },
                )
            )

    # --- сложенные отчёты WB ---------------------------------------------------------

    async def built_reports(self, seller_id: uuid.UUID) -> dict[int, int]:
        """Отчёт -> версия правила, по которой он сложен."""
        rows = await self.session.execute(
            select(WbReportModel.report_id, WbReportModel.version).where(WbReportModel.seller_id == seller_id)
        )
        return {int(report_id): int(version) for report_id, version in rows.all()}

    async def last_built_at(self, seller_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, datetime]:
        wanted = list(seller_ids)
        if not wanted:
            return {}
        rows = await self.session.execute(
            select(WbReportModel.seller_id, func.max(WbReportModel.built_at))
            .where(WbReportModel.seller_id.in_(wanted))
            .group_by(WbReportModel.seller_id)
        )
        return {seller_id: built_at for seller_id, built_at in rows.all()}

    async def save_report_facts(
        self,
        seller_id: uuid.UUID,
        report: SalesReport,
        totals: Iterable[SalesReportTotals],
        *,
        version: int,
        now: datetime,
    ) -> int:
        """Отчёт целиком: прежние суммы уходят, новые встают на их место той же транзакцией."""
        await self.session.execute(
            delete(WbFactModel).where(WbFactModel.seller_id == seller_id, WbFactModel.report_id == report.report_id)
        )
        rows = [{"seller_id": seller_id, **asdict(item)} for item in totals]
        chunk = max(1, min(_INSERT_CHUNK, _MAX_QUERY_PARAMETERS // max(len(rows[0]), 1))) if rows else _INSERT_CHUNK
        for offset in range(0, len(rows), chunk):
            await self.session.execute(insert(WbFactModel).values(rows[offset : offset + chunk]))
        statement = insert(WbReportModel).values(
            seller_id=seller_id,
            report_id=report.report_id,
            report_type=report.report_type,
            date_from=report.date_from,
            date_to=report.date_to,
            version=version,
            built_at=now,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "report_id"],
                set_={"version": statement.excluded.version, "built_at": statement.excluded.built_at},
            )
        )
        return len(rows)

    async def facts(self, seller_id: uuid.UUID, *, since: date, until: date) -> list[SalesReportTotals]:
        """Суммы отчётов кабинета, чей период пересекает окно."""
        reports = select(WbReportModel.report_id).where(
            WbReportModel.seller_id == seller_id, WbReportModel.date_to >= since, WbReportModel.date_from <= until
        )
        rows = await self.session.scalars(
            select(WbFactModel).where(WbFactModel.seller_id == seller_id, WbFactModel.report_id.in_(reports))
        )
        names = [field.name for field in fields(SalesReportTotals)]
        return [SalesReportTotals(**{name: getattr(row, name) for name in names}) for row in rows]
