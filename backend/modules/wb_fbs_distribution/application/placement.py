import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_fbs_distribution.domain import (
    BASIS_POINTS,
    Region,
    WarehouseSlot,
    priority_order,
    shares_are_whole,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository


class InvalidPlacementError(Exception):
    """Настройка, которую нельзя сохранить, не сломав расчёт."""


@dataclass(frozen=True, slots=True)
class OfficeRow:
    office_id: int
    name: str
    city: str
    address: str
    federal_district: str
    cargo_type: int
    region_code: str | None
    # Есть ли под этот объект склад хотя бы у одного подключённого кабинета.
    used_by_cabinets: int


@dataclass(frozen=True, slots=True)
class PlacementSettings:
    reserve_units: int
    priority_regions: int


@dataclass(frozen=True, slots=True)
class SetupOverview:
    """Всё, из чего оператор собирает схему распределения."""

    regions: list[Region]
    shares_ready: bool
    settings: PlacementSettings
    offices: list[OfficeRow]
    unassigned_offices: int


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """Место склада в очереди распределения кабинета."""

    place: int
    warehouse_id: int
    name: str
    city: str
    region_code: str | None
    region_title: str


class PlacementService:
    """Регионы, приоритеты и доли: схема, по которой потом считается план."""

    def __init__(self, session: AsyncSession, distribution: FbsDistributionRepository) -> None:
        self.session = session
        self.distribution = distribution

    async def setup(self) -> SetupOverview:
        regions = await self.distribution.regions()
        assignment = await self.distribution.office_regions()
        settings = await self.distribution.settings()
        offices = await self.distribution.offices()
        used = await self.distribution.offices_in_use()
        rows = [
            OfficeRow(
                office_id=office.office_id,
                name=office.name,
                city=office.city,
                address=office.address,
                federal_district=office.federal_district,
                cargo_type=office.cargo_type,
                region_code=assignment.get(office.office_id),
                used_by_cabinets=used.get(office.office_id, 0),
            )
            for office in offices
        ]
        return SetupOverview(
            regions=regions,
            shares_ready=shares_are_whole(regions),
            settings=PlacementSettings(
                reserve_units=settings.reserve_units, priority_regions=settings.priority_regions
            ),
            offices=rows,
            unassigned_offices=sum(1 for row in rows if row.region_code is None),
        )

    async def save_regions(self, ordered: list[tuple[str, int]]) -> SetupOverview:
        """Сохранить порядок направлений и их доли.

        Доли принимаются либо все нулевыми — логист их ещё не назвал, — либо
        складывающимися ровно в 100%. Промежуточное состояние не сохраняем:
        по неполным долям расчёт молча раздаст меньше, чем есть.
        """
        known = {region.code for region in await self.distribution.regions()}
        codes = [code for code, _ in ordered]
        if set(codes) != known:
            raise InvalidPlacementError("Направления заданы миграцией: их нельзя добавлять и удалять")
        if len(set(codes)) != len(codes):
            raise InvalidPlacementError("Направление указано дважды")
        total = sum(share for _, share in ordered)
        if any(share < 0 for _, share in ordered):
            raise InvalidPlacementError("Доля направления не может быть отрицательной")
        if total not in (0, BASIS_POINTS):
            raise InvalidPlacementError("Доли направлений должны складываться ровно в 100%")
        await self.distribution.save_regions(
            [
                Region(code=code, title="", position=position, share_bp=share)
                for position, (code, share) in enumerate(ordered)
            ]
        )
        await self.session.commit()
        return await self.setup()

    async def assign_office(self, office_id: int, region_code: str | None) -> SetupOverview:
        if region_code is not None:
            known = {region.code for region in await self.distribution.regions()}
            if region_code not in known:
                raise InvalidPlacementError("Неизвестное направление")
        await self.distribution.assign_office_region(office_id, region_code)
        await self.session.commit()
        return await self.setup()

    async def save_settings(self, *, reserve_units: int, priority_regions: int) -> SetupOverview:
        if reserve_units < 0:
            raise InvalidPlacementError("Резерв не может быть отрицательным")
        if priority_regions < 1:
            raise InvalidPlacementError("Приоритетных направлений должно быть хотя бы одно")
        await self.distribution.save_settings(reserve_units=reserve_units, priority_regions=priority_regions)
        await self.session.commit()
        return await self.setup()

    async def set_placement(
        self, seller_id: uuid.UUID, warehouse_id: int, *, participates: bool, position: int
    ) -> list[QueueEntry]:
        if position < 0:
            raise InvalidPlacementError("Место в очереди не может быть отрицательным")
        if not await self.distribution.set_warehouse_placement(
            seller_id, warehouse_id, participates=participates, position=position
        ):
            raise SellerNotFoundError(str(seller_id))
        await self.session.commit()
        return await self.queue(seller_id)

    async def queue(self, seller_id: uuid.UUID) -> list[QueueEntry]:
        """Очередь складов кабинета в том порядке, в котором их берёт расчёт."""
        regions = await self.distribution.regions()
        titles = {region.code: region.title for region in regions}
        assignment = await self.distribution.office_regions()
        warehouses = {
            warehouse.warehouse_id: warehouse
            for warehouse in await self.distribution.warehouses(seller_id)
            if warehouse.participates
        }
        offices = {office.office_id: office for office in await self.distribution.offices()}
        slots = [
            WarehouseSlot(
                warehouse_id=warehouse.warehouse_id,
                region_code=assignment.get(warehouse.office_id, ""),
                position=warehouse.position,
            )
            for warehouse in warehouses.values()
        ]
        entries = []
        for place, warehouse_id in enumerate(priority_order(slots, regions), start=1):
            warehouse = warehouses[warehouse_id]
            code = assignment.get(warehouse.office_id)
            office = offices.get(warehouse.office_id)
            entries.append(
                QueueEntry(
                    place=place,
                    warehouse_id=warehouse_id,
                    name=warehouse.name,
                    city=office.city if office else "",
                    region_code=code,
                    region_title=titles.get(code or "", "без направления"),
                )
            )
        return entries
