import uuid
from dataclasses import dataclass

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
class SellerOverview:
    """Состояние автоматизации по одному кабинету."""

    enrollment: SellerEnrollment


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
        return SellerOverview(enrollment=enrollment)

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
