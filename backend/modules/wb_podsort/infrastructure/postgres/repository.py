import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_podsort.domain import BarcodeInfo, DayCount, PodsortSettings
from backend.modules.wb_podsort.infrastructure.postgres.models import (
    BarcodeModel,
    OrderCountModel,
    OrderDayModel,
    SettingsModel,
    TrackedSellerModel,
    WarehouseRegionModel,
)

_CHUNK = 1000


@dataclass(frozen=True, slots=True)
class CountTotals:
    """Сводка заказов баркода в регионе за периоды книги — считается в базе."""

    barcode: str
    region: str
    months: tuple[int, ...]
    orders_14: int
    orders_7: int
    window: int
    window_fbs: int
    month_current: int


class PodsortRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- membership -------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def tracked_rows(self) -> list[TrackedSellerModel]:
        return list(await self.session.scalars(select(TrackedSellerModel).order_by(TrackedSellerModel.enrolled_at)))

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        for model in (OrderCountModel, OrderDayModel, BarcodeModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def still_tracked(self, seller_id: uuid.UUID) -> bool:
        """Подключён ли кабинет в момент записи, с блокировкой строки: отключение
        могло прийти, пока читался WB, и запись не должна воскресить стёртое."""
        found = await self.session.scalar(
            select(TrackedSellerModel.seller_id)
            .where(TrackedSellerModel.seller_id == seller_id)
            .with_for_update(read=True)
        )
        return found is not None

    # --- collection -------------------------------------------------------

    async def record_attempt(self, seller_id: uuid.UUID, *, now: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(attempted_at=now)
        )

    async def finish_collection(self, seller_id: uuid.UUID, *, now: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collected_at=now, collection_error=None)
        )

    async def clear_error(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(collection_error=None)
        )

    async def fail_collection(self, seller_id: uuid.UUID, error: str) -> None:
        """Прежние сутки остаются; меняется только текст ошибки."""
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collection_error=error[:1000])
        )

    async def loaded_days(self, seller_id: uuid.UUID, since: date) -> dict[date, datetime]:
        rows = await self.session.execute(
            select(OrderDayModel.day, OrderDayModel.loaded_at).where(
                OrderDayModel.seller_id == seller_id, OrderDayModel.day >= since
            )
        )
        return {day: loaded_at for day, loaded_at in rows.all()}

    async def replace_day(
        self,
        seller_id: uuid.UUID,
        day: date,
        counts: Sequence[DayCount],
        barcodes: Iterable[BarcodeInfo],
        *,
        now: datetime,
    ) -> None:
        """Сутки целиком: повторная загрузка того же дня заменяет его, а не прибавляет."""
        await self.session.execute(
            delete(OrderCountModel).where(OrderCountModel.seller_id == seller_id, OrderCountModel.day == day)
        )
        rows = [
            {
                "seller_id": seller_id,
                "day": day,
                "barcode": count.barcode,
                "region": count.region,
                "orders": count.orders,
                "fbs_orders": count.fbs_orders,
            }
            for count in counts
        ]
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(OrderCountModel).values(rows[offset : offset + _CHUNK]))
        statement = insert(OrderDayModel).values(
            seller_id=seller_id, day=day, orders=sum(count.orders for count in counts), loaded_at=now
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "day"],
                set_={"orders": statement.excluded.orders, "loaded_at": statement.excluded.loaded_at},
            )
        )
        await self._upsert_barcodes(seller_id, barcodes, now=now)

    async def _upsert_barcodes(self, seller_id: uuid.UUID, barcodes: Iterable[BarcodeInfo], *, now: datetime) -> None:
        rows = [
            {
                "seller_id": seller_id,
                "barcode": info.barcode,
                "nm_id": info.nm_id,
                "vendor_code": info.vendor_code,
                "subject": info.subject,
                "tech_size": info.tech_size,
                "seen_at": now,
            }
            for info in {info.barcode: info for info in barcodes}.values()
        ]
        for offset in range(0, len(rows), _CHUNK):
            statement = insert(BarcodeModel).values(rows[offset : offset + _CHUNK])
            excluded = statement.excluded
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "barcode"],
                    set_={
                        "nm_id": excluded.nm_id,
                        "vendor_code": excluded.vendor_code,
                        "subject": excluded.subject,
                        "tech_size": excluded.tech_size,
                        "seen_at": excluded.seen_at,
                    },
                )
            )

    async def prune(self, seller_id: uuid.UUID, before: date) -> None:
        for model in (OrderCountModel, OrderDayModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id, model.day < before))

    async def collection_summary(self, seller_ids: set[uuid.UUID]) -> tuple[datetime | None, int]:
        """Последний успех и число кабинетов с ошибкой."""
        if not seller_ids:
            return None, 0
        row = (
            await self.session.execute(
                select(
                    func.max(TrackedSellerModel.collected_at),
                    func.count().filter(TrackedSellerModel.collection_error.is_not(None)),
                ).where(TrackedSellerModel.seller_id.in_(seller_ids))
            )
        ).one()
        return row[0], int(row[1] or 0)

    # --- reading ----------------------------------------------------------

    async def totals(
        self,
        seller_id: uuid.UUID,
        *,
        months: Sequence[tuple[date, date]],
        last_day: date,
        window_start: date,
    ) -> list[CountTotals]:
        """Суммы по баркоду и региону за месяцы, 14 и 7 дней и окно расчёта — одним запросом."""
        day = OrderCountModel.day
        orders = OrderCountModel.orders

        def between(start: date, end: date):
            return func.coalesce(func.sum(orders).filter(day >= start, day <= end), 0)

        seven = last_day.toordinal() - 6
        fourteen = last_day.toordinal() - 13
        start = min(months[0][0], window_start, date.fromordinal(fourteen))
        columns = [between(month_start, month_end) for month_start, month_end in months]
        statement = (
            select(
                OrderCountModel.barcode,
                OrderCountModel.region,
                *columns,
                between(date.fromordinal(fourteen), last_day),
                between(date.fromordinal(seven), last_day),
                between(window_start, last_day),
                func.coalesce(func.sum(OrderCountModel.fbs_orders).filter(day >= window_start, day <= last_day), 0),
            )
            .where(OrderCountModel.seller_id == seller_id, day >= start, day <= last_day)
            .group_by(OrderCountModel.barcode, OrderCountModel.region)
        )
        found: list[CountTotals] = []
        for row in (await self.session.execute(statement)).all():
            values = [int(value) for value in row[2:]]
            month_values = tuple(values[: len(months)])
            found.append(
                CountTotals(
                    barcode=row[0],
                    region=row[1],
                    months=month_values,
                    orders_14=values[len(months)],
                    orders_7=values[len(months) + 1],
                    window=values[len(months) + 2],
                    window_fbs=values[len(months) + 3],
                    month_current=month_values[-1],
                )
            )
        return found

    async def barcodes(self, seller_id: uuid.UUID) -> dict[str, BarcodeInfo]:
        rows = await self.session.scalars(select(BarcodeModel).where(BarcodeModel.seller_id == seller_id))
        return {
            row.barcode: BarcodeInfo(row.barcode, row.nm_id, row.vendor_code, row.subject, row.tech_size)
            for row in rows
        }

    # --- settings ---------------------------------------------------------

    async def settings(self) -> PodsortSettings:
        row = await self.session.get(SettingsModel, 1)
        if row is None:
            return PodsortSettings()
        return PodsortSettings(window_days=row.window_days, cover_days=row.cover_days, regions=tuple(row.regions))

    async def save_settings(self, settings: PodsortSettings, *, updated_by: uuid.UUID | None) -> None:
        values = {
            "window_days": settings.window_days,
            "cover_days": settings.cover_days,
            "regions": list(settings.regions),
            "updated_at": datetime.now(UTC),
            "updated_by": updated_by,
        }
        statement = insert(SettingsModel).values(id=1, **values)
        await self.session.execute(statement.on_conflict_do_update(index_elements=["id"], set_=values))

    async def warehouse_overrides(self) -> dict[str, str | None]:
        rows = await self.session.execute(select(WarehouseRegionModel.warehouse_name, WarehouseRegionModel.region))
        return {name: region for name, region in rows.all()}

    async def set_warehouse_region(self, name: str, region: str | None) -> None:
        statement = insert(WarehouseRegionModel).values(
            warehouse_name=name, region=region, updated_at=datetime.now(UTC)
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["warehouse_name"],
                set_={"region": statement.excluded.region, "updated_at": statement.excluded.updated_at},
            )
        )

    async def reset_warehouse_region(self, name: str) -> None:
        await self.session.execute(delete(WarehouseRegionModel).where(WarehouseRegionModel.warehouse_name == name))
