import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_distribution.application.placement import PlacementService
from backend.modules.wb_fbs_distribution.domain import (
    BASIS_POINTS,
    AllocationTarget,
    SharesNotConfigured,
    allocate,
    available_units,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository

# Причины, по которым размер не попал в план. Пропуск без причины неотличим от
# «товара нет», а это разные вещи.
NO_STOCK = "no_stock"
NO_WAREHOUSES = "no_warehouses"
SHARES_MISSING = "shares_missing"
POOL_SPLIT_MISSING = "pool_split_missing"
POOL_MISSING = "pool_missing"

REASON_TEXT = {
    NO_STOCK: "остатка не осталось после резерва",
    NO_WAREHOUSES: "у кабинета нет складов в очереди распределения",
    SHARES_MISSING: "доли направлений не заданы, а остатка хватает на все склады",
    POOL_SPLIT_MISSING: "запас делят несколько кабинетов, правило деления не задано",
    POOL_MISSING: "позиции нет в снимке 1С",
}


@dataclass(frozen=True, slots=True)
class PlanItem:
    chrt_id: int
    item_id: str
    characteristic: str
    name: str
    barcode: str
    on_hand: int
    available: int
    amounts: dict[int, int]

    @property
    def units(self) -> int:
        return sum(self.amounts.values())


@dataclass(frozen=True, slots=True)
class PlanSkip:
    chrt_id: int
    item_id: str
    characteristic: str
    name: str
    reason: str

    @property
    def text(self) -> str:
        return REASON_TEXT.get(self.reason, self.reason)


@dataclass(frozen=True, slots=True)
class Plan:
    """Расчёт по одному кабинету. Ничего в WB не пишет."""

    id: uuid.UUID
    seller_id: uuid.UUID
    snapshot_id: uuid.UUID | None
    created_at: datetime
    reserve_units: int
    priority_regions: int
    warehouses: int
    items: list[PlanItem]
    skips: list[PlanSkip]

    @property
    def units(self) -> int:
        return sum(item.units for item in self.items)


class PlanningService:
    """Расчёт распределения. Всегда `dry run`: план считается и сохраняется."""

    def __init__(
        self,
        session: AsyncSession,
        distribution: FbsDistributionRepository,
        placement: PlacementService,
    ) -> None:
        self.session = session
        self.distribution = distribution
        self.placement = placement

    async def build(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> Plan:
        stamp = now or datetime.now(UTC)
        settings = await self.distribution.settings()
        regions = {region.code: region.share_bp for region in await self.distribution.regions()}
        assignment = await self.distribution.office_regions()

        warehouses = {row.warehouse_id: row for row in await self.distribution.warehouses(seller_id)}
        queue = [
            AllocationTarget(
                warehouse_id=entry.warehouse_id,
                region_code=assignment.get(warehouses[entry.warehouse_id].office_id, ""),
            )
            for entry in await self.placement.queue(seller_id)
            # Пока WB обрабатывает создание или удаление, склад не готов
            # принимать остатки, и план обязан его пропустить.
            if entry.warehouse_id in warehouses
            and not warehouses[entry.warehouse_id].is_processing
            and not warehouses[entry.warehouse_id].is_deleting
        ]

        mappings = await self.distribution.mappings(seller_id)
        pools = {
            (pool.item_id, pool.characteristic): pool
            for pool in await self.distribution.pools_by_key(
                [(mapping.item_id, mapping.characteristic) for mapping in mappings]
            )
        }
        seller_counts = await self.distribution.pool_seller_counts()
        pool_shares = await self.distribution.pool_shares()
        snapshot = await self.distribution.latest_snapshot()

        items: list[PlanItem] = []
        skips: list[PlanSkip] = []
        for mapping in mappings:
            key = (mapping.item_id, mapping.characteristic)
            pool = pools.get(key)
            if pool is None:
                skips.append(self._skip(mapping, "", POOL_MISSING))
                continue

            available = available_units(pool.quantity, settings.reserve_units)
            share = self._cabinet_share(key, seller_id, seller_counts, pool_shares)
            if share is None:
                skips.append(self._skip(mapping, pool.name, POOL_SPLIT_MISSING))
                continue
            available = int(Fraction(available) * Fraction(share, BASIS_POINTS))

            if available <= 0:
                skips.append(self._skip(mapping, pool.name, NO_STOCK))
                continue
            if not queue:
                skips.append(self._skip(mapping, pool.name, NO_WAREHOUSES))
                continue
            try:
                amounts = allocate(available, queue, regions, priority_regions=settings.priority_regions)
            except SharesNotConfigured:
                skips.append(self._skip(mapping, pool.name, SHARES_MISSING))
                continue

            items.append(
                PlanItem(
                    chrt_id=mapping.chrt_id,
                    item_id=mapping.item_id,
                    characteristic=mapping.characteristic,
                    name=pool.name,
                    barcode=pool.barcode,
                    on_hand=pool.quantity,
                    available=available,
                    amounts=amounts,
                )
            )

        plan_id = await self.distribution.save_plan(
            seller_id=seller_id,
            snapshot_id=snapshot.id if snapshot else None,
            created_at=stamp,
            reserve_units=settings.reserve_units,
            priority_regions=settings.priority_regions,
            warehouses=len(queue),
            items=[(item.chrt_id, item.amounts) for item in items],
            skips=[(skip.chrt_id, skip.item_id, skip.characteristic, skip.reason) for skip in skips],
        )
        await self.session.commit()
        return Plan(
            id=plan_id,
            seller_id=seller_id,
            snapshot_id=snapshot.id if snapshot else None,
            created_at=stamp,
            reserve_units=settings.reserve_units,
            priority_regions=settings.priority_regions,
            warehouses=len(queue),
            items=items,
            skips=skips,
        )

    @staticmethod
    def _cabinet_share(
        key: tuple[str, str],
        seller_id: uuid.UUID,
        counts: dict[tuple[str, str], int],
        shares: dict[tuple[str, str], dict[uuid.UUID, int]],
    ) -> int | None:
        """Какая доля пула принадлежит этому кабинету.

        Пул одного кабинета берётся целиком. Общий пул без правила деления
        возвращает None: без правила каждый кабинет забрал бы весь остаток, и
        вместе они пообещали бы WB кратное количество.
        """
        if counts.get(key, 1) <= 1:
            return BASIS_POINTS
        rule = shares.get(key)
        if not rule or sum(rule.values()) != BASIS_POINTS:
            return None
        return rule.get(seller_id, 0)

    @staticmethod
    def _skip(mapping, name: str, reason: str) -> PlanSkip:
        return PlanSkip(
            chrt_id=mapping.chrt_id,
            item_id=mapping.item_id,
            characteristic=mapping.characteristic,
            name=name,
            reason=reason,
        )
