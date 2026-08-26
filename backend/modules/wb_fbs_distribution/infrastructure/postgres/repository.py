import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_distribution.domain import SellerEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres.models import (
    SellerWarehouseModel,
    TrackedSellerModel,
    WBOfficeModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import Office, SellerWarehouse


class FbsDistributionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- подключение ------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(SellerWarehouseModel).where(SellerWarehouseModel.seller_id == seller_id))
        await self.untrack(seller_id)

    async def tracked_row(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def tracked(self, seller_id: uuid.UUID) -> bool:
        """Пережил ли кабинет сетевую паузу: писать за отключённого нельзя."""
        return await self.session.get(TrackedSellerModel, seller_id) is not None

    async def enrollment(self, seller_id: uuid.UUID) -> SellerEnrollment | None:
        row = await self.session.get(TrackedSellerModel, seller_id)
        if row is None:
            return None
        return SellerEnrollment(
            seller_id=row.seller_id,
            enrolled_at=row.enrolled_at,
            write_enabled=row.write_enabled,
        )

    async def set_write_enabled(self, seller_id: uuid.UUID, enabled: bool) -> bool:
        """Переключить режим записи. Возвращает False, если селлер не подключён."""
        result = await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(write_enabled=enabled)
            .returning(TrackedSellerModel.seller_id)
        )
        return result.scalar_one_or_none() is not None

    # --- зеркало WB ------------------------------------------------------

    async def replace_offices(self, offices: Sequence[Office], *, now: datetime | None = None) -> None:
        """Обновить общий справочник объектов.

        Объекты не удаляются, даже если пропали из ответа: на них могут
        ссылаться уже созданные склады, а исчезновение из справочника само по
        себе ещё не значит, что объект закрыт. Устаревшую запись видно по
        `synced_at`.
        """
        if not offices:
            return
        stamp = now or datetime.now(UTC)
        rows = [
            {
                "office_id": office.office_id,
                "name": office.name,
                "city": office.city,
                "address": office.address,
                "federal_district": office.federal_district,
                "longitude": office.longitude,
                "latitude": office.latitude,
                "cargo_type": office.cargo_type,
                "delivery_type": office.delivery_type,
                "synced_at": stamp,
            }
            for office in offices
        ]
        statement = insert(WBOfficeModel).values(rows)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["office_id"],
                set_={
                    column: statement.excluded[column]
                    for column in (
                        "name",
                        "city",
                        "address",
                        "federal_district",
                        "longitude",
                        "latitude",
                        "cargo_type",
                        "delivery_type",
                        "synced_at",
                    )
                },
            )
        )

    async def offices_synced_at(self) -> datetime | None:
        return await self.session.scalar(select(func.max(WBOfficeModel.synced_at)))

    async def offices(self) -> list[WBOfficeModel]:
        return list(await self.session.scalars(select(WBOfficeModel).order_by(WBOfficeModel.city, WBOfficeModel.name)))

    async def replace_warehouses(
        self, seller_id: uuid.UUID, warehouses: Sequence[SellerWarehouse], *, now: datetime | None = None
    ) -> None:
        """Привести зеркало складов кабинета к тому, что вернул WB.

        Склад, пропавший из ответа, удаляется из зеркала: его больше нет в
        кабинете, и держать его в списке — значит потом посчитать на него план.
        """
        stamp = now or datetime.now(UTC)
        keep = [warehouse.warehouse_id for warehouse in warehouses]
        condition = SellerWarehouseModel.seller_id == seller_id
        if keep:
            condition = condition & SellerWarehouseModel.warehouse_id.not_in(keep)
        await self.session.execute(delete(SellerWarehouseModel).where(condition))
        if not warehouses:
            return
        rows = [
            {
                "seller_id": seller_id,
                "warehouse_id": warehouse.warehouse_id,
                "office_id": warehouse.office_id,
                "store_id": warehouse.store_id,
                "name": warehouse.name,
                "cargo_type": warehouse.cargo_type,
                "delivery_type": warehouse.delivery_type,
                "is_deleting": warehouse.is_deleting,
                "is_processing": warehouse.is_processing,
                "synced_at": stamp,
            }
            for warehouse in warehouses
        ]
        statement = insert(SellerWarehouseModel).values(rows)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "warehouse_id"],
                set_={
                    column: statement.excluded[column]
                    for column in (
                        "office_id",
                        "store_id",
                        "name",
                        "cargo_type",
                        "delivery_type",
                        "is_deleting",
                        "is_processing",
                        "synced_at",
                    )
                },
            )
        )
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(warehouses_synced_at=stamp)
        )

    async def sellers_due_for_sync(self, before: datetime) -> list[uuid.UUID]:
        """Кабинеты, чьё зеркало старше очередного срока сверки.

        Условие по времени, а не журнал занятых слотов: сверка идемпотентна,
        и повтор после перезапуска ничего не портит.
        """
        return list(
            await self.session.scalars(
                select(TrackedSellerModel.seller_id)
                .where(
                    (TrackedSellerModel.warehouses_synced_at.is_(None))
                    | (TrackedSellerModel.warehouses_synced_at < before)
                )
                .order_by(TrackedSellerModel.enrolled_at)
            )
        )

    async def warehouses(self, seller_id: uuid.UUID) -> list[SellerWarehouseModel]:
        return list(
            await self.session.scalars(
                select(SellerWarehouseModel)
                .where(SellerWarehouseModel.seller_id == seller_id)
                .order_by(SellerWarehouseModel.name)
            )
        )
