import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.domain import CostPrice, OzonFact, Stock
from backend.modules.fin_reports.infrastructure.postgres.models import (
    CostPriceModel,
    OzonDayModel,
    OzonFactModel,
    TrackedSellerModel,
    WbFactModel,
    WbReportModel,
    WbStockSnapshotModel,
)
from backend.modules.wb_core.domain import OzonAccrualLine, SalesReport, SalesReportTotals

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
        await self.session.execute(delete(WbStockSnapshotModel).where(WbStockSnapshotModel.seller_id == seller_id))
        await self.session.execute(delete(OzonFactModel).where(OzonFactModel.seller_id == seller_id))
        await self.session.execute(delete(OzonDayModel).where(OzonDayModel.seller_id == seller_id))
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

    # --- снимки остатков ---------------------------------------------------------------

    async def snapshot_weeks(self, seller_id: uuid.UUID) -> set[date]:
        rows = await self.session.scalars(
            select(WbStockSnapshotModel.week_end).where(WbStockSnapshotModel.seller_id == seller_id).distinct()
        )
        return set(rows)

    async def save_stock_snapshot(
        self, seller_id: uuid.UUID, week_end: date, stocks: Mapping[tuple[int, str], Stock], *, now: datetime
    ) -> int:
        """Снимок недели целиком; повторный снимок той же недели переписывает прежний."""
        await self.session.execute(
            delete(WbStockSnapshotModel).where(
                WbStockSnapshotModel.seller_id == seller_id, WbStockSnapshotModel.week_end == week_end
            )
        )
        rows = [
            {
                "seller_id": seller_id,
                "week_end": week_end,
                "nm_id": nm_id,
                "tech_size": tech_size,
                "in_warehouse": stock.in_warehouse,
                "to_client": stock.to_client,
                "from_client": stock.from_client,
                "total": stock.total,
                "taken_at": now,
            }
            for (nm_id, tech_size), stock in stocks.items()
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            await self.session.execute(insert(WbStockSnapshotModel).values(rows[offset : offset + _INSERT_CHUNK]))
        return len(rows)

    async def stock_snapshot(self, seller_id: uuid.UUID, week_end: date) -> dict[tuple[int, str], Stock] | None:
        """`None` — снимка этой недели нет (неделя раньше первого снимка или ещё не закрыта)."""
        rows = list(
            await self.session.scalars(
                select(WbStockSnapshotModel).where(
                    WbStockSnapshotModel.seller_id == seller_id, WbStockSnapshotModel.week_end == week_end
                )
            )
        )
        if not rows:
            return None
        return {
            (row.nm_id, row.tech_size): Stock(row.in_warehouse, row.to_client, row.from_client, row.total)
            for row in rows
        }

    # --- сложенные начисления Ozon ---------------------------------------------------------

    async def built_ozon_days(self, seller_id: uuid.UUID) -> dict[date, int]:
        rows = await self.session.execute(
            select(OzonDayModel.day, OzonDayModel.version).where(OzonDayModel.seller_id == seller_id)
        )
        return {day: int(version) for day, version in rows.all()}

    async def last_ozon_built_at(self, seller_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(
            select(func.max(OzonDayModel.built_at)).where(OzonDayModel.seller_id == seller_id)
        )

    async def save_ozon_day(
        self, seller_id: uuid.UUID, day: date, lines: Iterable[OzonAccrualLine], *, version: int, now: datetime
    ) -> int:
        """День целиком: строки складываются по SKU, виду и типу; прежние суммы дня уходят."""
        await self.session.execute(
            delete(OzonFactModel).where(OzonFactModel.seller_id == seller_id, OzonFactModel.day == day)
        )
        folded: dict[tuple[int, str, int], list[Any]] = {}
        for line in lines:
            values = folded.setdefault((line.sku, line.line, line.type_id), [0, *([Decimal("0.00")] * 6)])
            values[0] += line.quantity
            values[1] += line.amount
            values[2] += line.sale_amount
            values[3] += line.sale_price
            values[4] += line.sale_commission
            values[5] += line.bonus
            values[6] += line.coinvestment
        rows = [
            {
                "seller_id": seller_id,
                "day": day,
                "sku": sku,
                "line": kind,
                "type_id": type_id,
                "quantity": values[0],
                "amount": values[1],
                "sale_amount": values[2],
                "sale_price": values[3],
                "sale_commission": values[4],
                "bonus": values[5],
                "coinvestment": values[6],
            }
            for (sku, kind, type_id), values in folded.items()
        ]
        for offset in range(0, len(rows), _INSERT_CHUNK):
            await self.session.execute(insert(OzonFactModel).values(rows[offset : offset + _INSERT_CHUNK]))
        statement = insert(OzonDayModel).values(seller_id=seller_id, day=day, version=version, built_at=now)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "day"],
                set_={"version": statement.excluded.version, "built_at": statement.excluded.built_at},
            )
        )
        return len(rows)

    async def ozon_facts(self, seller_id: uuid.UUID, *, since: date, until: date) -> list[OzonFact]:
        rows = await self.session.scalars(
            select(OzonFactModel).where(
                OzonFactModel.seller_id == seller_id, OzonFactModel.day >= since, OzonFactModel.day <= until
            )
        )
        names = [field.name for field in fields(OzonFact)]
        return [OzonFact(**{name: getattr(row, name) for name in names}) for row in rows]
