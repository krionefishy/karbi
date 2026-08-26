import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_distribution.domain import SellerEnrollment
from backend.modules.wb_fbs_distribution.infrastructure.postgres.models import TrackedSellerModel


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
        await self.untrack(seller_id)

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
