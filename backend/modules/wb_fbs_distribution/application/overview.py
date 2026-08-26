import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_fbs_distribution.domain import SellerEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository


@dataclass(frozen=True, slots=True)
class DistributionCatalogOverview:
    """Строка автоматизации в общем каталоге."""

    seller_count: int

    @property
    def status(self) -> str:
        # Пока модуль ничего не запускает, состояние честнее показывать как
        # «подключено, но не работало», а не выдумывать успех.
        return "idle"


@dataclass(frozen=True, slots=True)
class WarehouseRow:
    """Виртуальный склад кабинета вместе с объектом, к которому он привязан.

    Объект может быть неизвестен: справочник и склады приезжают двумя разными
    запросами, и WB волен вернуть склад на объект, которого в справочнике этого
    ключа нет. Пустые поля объекта честнее, чем пропущенная строка.
    """

    warehouse_id: int
    office_id: int
    name: str
    city: str
    address: str
    federal_district: str
    cargo_type: int
    is_processing: bool
    is_deleting: bool
    participates: bool
    position: int
    region_code: str | None


@dataclass(frozen=True, slots=True)
class SellerOverview:
    """Состояние автоматизации по одному кабинету."""

    enrollment: SellerEnrollment
    warehouses_synced_at: datetime | None
    offices_known: int
    warehouses: list[WarehouseRow]


class FbsDistributionService:
    """Что интерфейс спрашивает у автоматизации: состояние и режим кабинета."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        distribution: FbsDistributionRepository,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.distribution = distribution

    async def overview(self) -> DistributionCatalogOverview:
        tracked = await self.distribution.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        return DistributionCatalogOverview(seller_count=len(tracked & active))

    async def seller_overview(self, seller_id: uuid.UUID) -> SellerOverview:
        enrollment = await self.distribution.enrollment(seller_id)
        if enrollment is None:
            raise SellerNotFoundError(str(seller_id))
        offices = {office.office_id: office for office in await self.distribution.offices()}
        assignment = await self.distribution.office_regions()
        rows = []
        for warehouse in await self.distribution.warehouses(seller_id):
            office = offices.get(warehouse.office_id)
            rows.append(
                WarehouseRow(
                    warehouse_id=warehouse.warehouse_id,
                    office_id=warehouse.office_id,
                    name=warehouse.name,
                    city=office.city if office else "",
                    address=office.address if office else "",
                    federal_district=office.federal_district if office else "",
                    cargo_type=warehouse.cargo_type,
                    is_processing=warehouse.is_processing,
                    is_deleting=warehouse.is_deleting,
                    participates=warehouse.participates,
                    position=warehouse.position,
                    region_code=assignment.get(warehouse.office_id),
                )
            )
        tracked = await self.distribution.tracked_row(seller_id)
        return SellerOverview(
            enrollment=enrollment,
            warehouses_synced_at=tracked.warehouses_synced_at if tracked else None,
            offices_known=len(offices),
            warehouses=rows,
        )

    async def set_write_enabled(self, seller_id: uuid.UUID, enabled: bool) -> SellerOverview:
        """Разрешить или запретить автоматизации писать остатки в кабинет.

        Отдельным действием, а не побочным эффектом подключения: право
        переписывать остатки живого кабинета включается осознанно и по одному,
        потому что пилот идёт не на всех сразу.
        """
        if not await self.distribution.set_write_enabled(seller_id, enabled):
            raise SellerNotFoundError(str(seller_id))
        await self.session.commit()
        return await self.seller_overview(seller_id)
