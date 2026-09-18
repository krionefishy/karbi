import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_review_chats.infrastructure.postgres.models import TrackedSellerModel


class ReviewChatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def is_tracked(self, seller_id: uuid.UUID) -> bool:
        return await self.session.get(TrackedSellerModel, seller_id) is not None

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))
