import uuid
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.domain import CostPrice
from backend.modules.fin_reports.infrastructure.postgres.models import CostPriceModel, TrackedSellerModel

_INSERT_CHUNK = 1000


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
