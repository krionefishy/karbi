import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_distribution.domain import Region, SellerEnrollment, StockLine
from backend.modules.wb_fbs_distribution.infrastructure.postgres.models import (
    AllocationItemModel,
    AllocationPlanModel,
    AllocationSkipModel,
    DistributionSettingsModel,
    OfficeRegionModel,
    PoolSellerShareModel,
    ProductMappingModel,
    PublishedStockModel,
    RegionModel,
    SellerWarehouseModel,
    StockPoolModel,
    StockPublicationModel,
    StockSnapshotModel,
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
        """Стереть всё, что модуль знает о кабинете.

        Список полный намеренно: реестр селлеров зовёт этот метод и не знает,
        что у нас внутри. Забытая здесь таблица не уронит ничего сразу — она
        просто тихо переживёт удаление кабинета.
        """
        plans = select(AllocationPlanModel.id).where(AllocationPlanModel.seller_id == seller_id)
        for child in (AllocationItemModel, AllocationSkipModel):
            await self.session.execute(delete(child).where(child.plan_id.in_(plans)))
        for model in (
            AllocationPlanModel,
            PoolSellerShareModel,
            ProductMappingModel,
            PublishedStockModel,
            SellerWarehouseModel,
            StockPublicationModel,
        ):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
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

    # --- регионы, разметка и настройки -----------------------------------

    async def regions(self) -> list[Region]:
        rows = await self.session.scalars(select(RegionModel).order_by(RegionModel.position))
        return [Region(code=row.code, title=row.title, position=row.position, share_bp=row.share_bp) for row in rows]

    async def save_regions(self, regions: Sequence[Region]) -> None:
        """Переписать порядок и доли направлений.

        Направления не создаются и не удаляются: их шесть, они заданы миграцией.
        Меняются только место в приоритете и доля.
        """
        for region in regions:
            await self.session.execute(
                update(RegionModel)
                .where(RegionModel.code == region.code)
                .values(position=region.position, share_bp=region.share_bp)
            )

    async def offices_in_use(self) -> dict[int, int]:
        """Сколько подключённых кабинетов уже имеют склад под каждым объектом.

        Оператору важно видеть, какие объекты уже освоены, а какие в справочнике
        есть, но никем не заняты.
        """
        rows = await self.session.execute(
            select(SellerWarehouseModel.office_id, func.count()).group_by(SellerWarehouseModel.office_id)
        )
        return {office_id: count for office_id, count in rows}

    async def office_regions(self) -> dict[int, str]:
        rows = await self.session.execute(select(OfficeRegionModel.office_id, OfficeRegionModel.region_code))
        return {office_id: region_code for office_id, region_code in rows}

    async def assign_office_region(self, office_id: int, region_code: str | None) -> None:
        """Отнести объект к направлению или снять разметку."""
        if region_code is None:
            await self.session.execute(delete(OfficeRegionModel).where(OfficeRegionModel.office_id == office_id))
            return
        statement = insert(OfficeRegionModel).values(office_id=office_id, region_code=region_code)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["office_id"], set_={"region_code": statement.excluded.region_code}
            )
        )

    async def set_warehouse_placement(
        self, seller_id: uuid.UUID, warehouse_id: int, *, participates: bool, position: int
    ) -> bool:
        result = await self.session.execute(
            update(SellerWarehouseModel)
            .where((SellerWarehouseModel.seller_id == seller_id) & (SellerWarehouseModel.warehouse_id == warehouse_id))
            .values(participates=participates, position=position)
            .returning(SellerWarehouseModel.warehouse_id)
        )
        return result.scalar_one_or_none() is not None

    async def settings(self) -> DistributionSettingsModel:
        row = await self.session.get(DistributionSettingsModel, 1)
        if row is None:
            # Строку заводит миграция; её отсутствие означает недокатанную базу,
            # а не повод считать по молчаливым умолчаниям.
            raise RuntimeError("Настройки распределения не заведены: не докатана миграция модуля")
        return row

    async def save_settings(self, *, reserve_units: int, priority_regions: int) -> DistributionSettingsModel:
        await self.session.execute(
            update(DistributionSettingsModel)
            .where(DistributionSettingsModel.id == 1)
            .values(
                reserve_units=reserve_units,
                priority_regions=priority_regions,
                updated_at=datetime.now(UTC),
            )
        )
        return await self.settings()

    # --- снимки 1С и пулы -------------------------------------------------

    async def record_snapshot(
        self,
        *,
        source: str,
        generated_at: datetime,
        received_at: datetime,
        lines: int,
        status: str,
        error: str | None,
    ) -> uuid.UUID:
        snapshot_id = uuid.uuid4()
        self.session.add(
            StockSnapshotModel(
                id=snapshot_id,
                source=source,
                generated_at=generated_at,
                received_at=received_at,
                lines=lines,
                status=status,
                error=error,
            )
        )
        await self.session.flush()
        return snapshot_id

    async def latest_snapshot(self) -> StockSnapshotModel | None:
        """Последний принятый снимок. Отклонённые в расчёт не идут."""
        return await self.session.scalar(
            select(StockSnapshotModel)
            .where(StockSnapshotModel.status == "accepted")
            .order_by(StockSnapshotModel.received_at.desc())
            .limit(1)
        )

    async def snapshot_history(self, limit: int = 20) -> list[StockSnapshotModel]:
        return list(
            await self.session.scalars(
                select(StockSnapshotModel).order_by(StockSnapshotModel.received_at.desc()).limit(limit)
            )
        )

    async def replace_pools(
        self, lines: Sequence[StockLine], *, snapshot_id: uuid.UUID, now: datetime | None = None
    ) -> None:
        """Привести пулы к абсолютному снимку.

        Пул, которого в снимке нет, обнуляется, а не удаляется: в 1С товара
        больше нет, значит на WB его должно стать ноль, но строка держит на себе
        сопоставление с карточкой, и терять его при каждой пропаже нельзя.
        """
        stamp = now or datetime.now(UTC)
        if lines:
            rows = [
                {
                    "item_id": line.item_id,
                    "characteristic": line.characteristic,
                    "barcode": line.barcode,
                    "name": line.name,
                    "quantity": line.quantity,
                    "snapshot_id": snapshot_id,
                    "updated_at": stamp,
                }
                for line in lines
            ]
            statement = insert(StockPoolModel).values(rows)
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["item_id", "characteristic"],
                    set_={
                        column: statement.excluded[column]
                        for column in ("barcode", "name", "quantity", "snapshot_id", "updated_at")
                    },
                )
            )
        await self.session.execute(
            update(StockPoolModel)
            .where(StockPoolModel.snapshot_id.is_distinct_from(snapshot_id))
            .values(quantity=0, snapshot_id=snapshot_id, updated_at=stamp)
        )

    async def pool_totals(self, *, reserve_units: int) -> tuple[int, int, int]:
        """Сколько пулов, сколько всего единиц и сколько из них раздаётся.

        Доступное считается тем же выражением, что и в домене: сумму по всей
        номенклатуре незачем тянуть в питон построчно.
        """
        available = func.least(
            StockPoolModel.quantity,
            func.greatest(reserve_units, StockPoolModel.quantity - reserve_units),
        )
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(StockPoolModel.quantity), 0),
                    func.coalesce(func.sum(available), 0),
                )
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2])

    async def pools(self, *, limit: int = 200, search: str = "") -> list[StockPoolModel]:
        statement = select(StockPoolModel).order_by(StockPoolModel.name, StockPoolModel.characteristic)
        if search:
            pattern = f"%{search.strip()}%"
            statement = statement.where(
                StockPoolModel.name.ilike(pattern)
                | StockPoolModel.barcode.ilike(pattern)
                | StockPoolModel.item_id.ilike(pattern)
            )
        return list(await self.session.scalars(statement.limit(limit)))

    # --- сопоставление товаров -------------------------------------------

    async def replace_mappings(
        self, seller_id: uuid.UUID, rows: Sequence[dict], *, now: datetime | None = None
    ) -> None:
        """Переписать связи кабинета целиком.

        Целиком, а не по одной: пропавший баркод должен исчезнуть из связей, а
        не остаться указывать на размер, которого в каталоге больше нет.
        """
        stamp = now or datetime.now(UTC)
        await self.session.execute(delete(ProductMappingModel).where(ProductMappingModel.seller_id == seller_id))
        if not rows:
            return
        self.session.add_all(
            [
                ProductMappingModel(
                    seller_id=seller_id,
                    chrt_id=row["chrt_id"],
                    item_id=row["item_id"],
                    characteristic=row["characteristic"],
                    barcode=row["barcode"],
                    article=row["article"],
                    matched_at=stamp,
                )
                for row in rows
            ]
        )

    async def mappings(self, seller_id: uuid.UUID | None = None) -> list[ProductMappingModel]:
        statement = select(ProductMappingModel)
        if seller_id is not None:
            statement = statement.where(ProductMappingModel.seller_id == seller_id)
        return list(await self.session.scalars(statement.order_by(ProductMappingModel.item_id)))

    async def mapped_pool_keys(self) -> set[tuple[str, str]]:
        rows = await self.session.execute(
            select(ProductMappingModel.item_id, ProductMappingModel.characteristic).distinct()
        )
        return {(item_id, characteristic) for item_id, characteristic in rows}

    async def pools_by_key(self, keys: Sequence[tuple[str, str]]) -> list[StockPoolModel]:
        if not keys:
            return []
        condition = tuple_(StockPoolModel.item_id, StockPoolModel.characteristic).in_(keys)
        return list(await self.session.scalars(select(StockPoolModel).where(condition)))

    async def unmapped_pools(self, *, limit: int = 200) -> list[StockPoolModel]:
        """Пулы, которым не нашлось ни одного размера ни в одном кабинете."""
        mapped = select(ProductMappingModel.item_id, ProductMappingModel.characteristic)
        condition = tuple_(StockPoolModel.item_id, StockPoolModel.characteristic).not_in(mapped)
        return list(
            await self.session.scalars(
                select(StockPoolModel).where(condition).order_by(StockPoolModel.name).limit(limit)
            )
        )

    async def pool_seller_counts(self) -> dict[tuple[str, str], int]:
        """Сколько кабинетов расходуют каждый пул."""
        rows = await self.session.execute(
            select(
                ProductMappingModel.item_id,
                ProductMappingModel.characteristic,
                func.count(ProductMappingModel.seller_id.distinct()),
            ).group_by(ProductMappingModel.item_id, ProductMappingModel.characteristic)
        )
        return {(item_id, characteristic): count for item_id, characteristic, count in rows}

    async def pool_shares(self) -> dict[tuple[str, str], dict[uuid.UUID, int]]:
        shares: dict[tuple[str, str], dict[uuid.UUID, int]] = {}
        for row in await self.session.scalars(select(PoolSellerShareModel)):
            shares.setdefault((row.item_id, row.characteristic), {})[row.seller_id] = row.share_bp
        return shares

    async def save_pool_shares(self, item_id: str, characteristic: str, shares: dict[uuid.UUID, int]) -> None:
        await self.session.execute(
            delete(PoolSellerShareModel).where(
                (PoolSellerShareModel.item_id == item_id) & (PoolSellerShareModel.characteristic == characteristic)
            )
        )
        self.session.add_all(
            [
                PoolSellerShareModel(
                    item_id=item_id, characteristic=characteristic, seller_id=seller_id, share_bp=share
                )
                for seller_id, share in shares.items()
            ]
        )

    # --- планы -----------------------------------------------------------

    async def save_plan(
        self,
        *,
        seller_id: uuid.UUID,
        snapshot_id: uuid.UUID | None,
        created_at: datetime,
        reserve_units: int,
        priority_regions: int,
        warehouses: int,
        items: Sequence[tuple[int, dict[int, int]]],
        skips: Sequence[tuple[int, str, str, str]],
    ) -> uuid.UUID:
        """Сохранить расчёт как есть. План неизменяем: новый расчёт — новая строка."""
        plan_id = uuid.uuid4()
        self.session.add(
            AllocationPlanModel(
                id=plan_id,
                seller_id=seller_id,
                snapshot_id=snapshot_id,
                created_at=created_at,
                reserve_units=reserve_units,
                priority_regions=priority_regions,
                warehouses=warehouses,
                items=len(items),
                units=sum(sum(amounts.values()) for _, amounts in items),
                skipped=len(skips),
            )
        )
        self.session.add_all(
            [
                AllocationItemModel(plan_id=plan_id, chrt_id=chrt_id, warehouse_id=warehouse_id, amount=amount)
                for chrt_id, amounts in items
                for warehouse_id, amount in amounts.items()
            ]
        )
        self.session.add_all(
            [
                AllocationSkipModel(
                    plan_id=plan_id,
                    chrt_id=chrt_id,
                    item_id=item_id,
                    characteristic=characteristic,
                    reason=reason,
                )
                for chrt_id, item_id, characteristic, reason in skips
            ]
        )
        await self.session.flush()
        return plan_id

    async def latest_plan(self, seller_id: uuid.UUID) -> AllocationPlanModel | None:
        """Самый свежий план кабинета.

        Порядок по возрастающему номеру, а не по времени: два расчёта могут
        попасть в одну отметку, и тогда «последним» оказался бы произвольный —
        то есть в WB уехал бы устаревший план.
        """
        return await self.session.scalar(
            select(AllocationPlanModel)
            .where(AllocationPlanModel.seller_id == seller_id)
            .order_by(AllocationPlanModel.sequence.desc())
            .limit(1)
        )

    async def plan_items(self, plan_id: uuid.UUID) -> list[AllocationItemModel]:
        return list(
            await self.session.scalars(
                select(AllocationItemModel)
                .where(AllocationItemModel.plan_id == plan_id)
                .order_by(AllocationItemModel.chrt_id, AllocationItemModel.warehouse_id)
            )
        )

    async def plan_skips(self, plan_id: uuid.UUID) -> list[AllocationSkipModel]:
        return list(
            await self.session.scalars(select(AllocationSkipModel).where(AllocationSkipModel.plan_id == plan_id))
        )

    # --- публикация остатков ---------------------------------------------

    async def published(self, seller_id: uuid.UUID) -> dict[tuple[int, str], int]:
        """Последнее подтверждённое чтением состояние складов кабинета."""
        rows = await self.session.execute(
            select(PublishedStockModel.warehouse_id, PublishedStockModel.sku, PublishedStockModel.amount).where(
                PublishedStockModel.seller_id == seller_id
            )
        )
        return {(warehouse_id, sku): amount for warehouse_id, sku, amount in rows}

    async def confirm_published(
        self,
        seller_id: uuid.UUID,
        warehouse_id: int,
        amounts: dict[str, int],
        *,
        now: datetime | None = None,
    ) -> None:
        """Записать то, что WB подтвердил вычиткой, а не то, что мы отправили."""
        stamp = now or datetime.now(UTC)
        if not amounts:
            return
        rows = [
            {
                "seller_id": seller_id,
                "warehouse_id": warehouse_id,
                "sku": sku,
                "amount": amount,
                "confirmed_at": stamp,
            }
            for sku, amount in amounts.items()
        ]
        statement = insert(PublishedStockModel).values(rows)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "warehouse_id", "sku"],
                set_={"amount": statement.excluded.amount, "confirmed_at": statement.excluded.confirmed_at},
            )
        )

    async def record_publication(
        self,
        *,
        seller_id: uuid.UUID,
        plan_id: uuid.UUID | None,
        warehouse_id: int,
        created_at: datetime,
        rows: int,
        status: str,
        drift: int = 0,
        error: str | None = None,
    ) -> uuid.UUID:
        publication_id = uuid.uuid4()
        self.session.add(
            StockPublicationModel(
                id=publication_id,
                seller_id=seller_id,
                plan_id=plan_id,
                warehouse_id=warehouse_id,
                created_at=created_at,
                rows=rows,
                status=status,
                drift=drift,
                error=error,
            )
        )
        await self.session.flush()
        return publication_id

    async def publication_history(self, seller_id: uuid.UUID, limit: int = 50) -> list[StockPublicationModel]:
        return list(
            await self.session.scalars(
                select(StockPublicationModel)
                .where(StockPublicationModel.seller_id == seller_id)
                .order_by(StockPublicationModel.created_at.desc())
                .limit(limit)
            )
        )
