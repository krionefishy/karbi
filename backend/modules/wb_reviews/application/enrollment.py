import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository

AUTOMATION_ID = "wb-reviews"
TITLE = "Мониторинг отзывов Wildberries"
DESCRIPTION = "Ежедневные снимки отзывов по подключённым товарам и селлерам Wildberries."


class ReviewsEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, reviews: ReviewSyncRepository) -> None:
        self.reviews = reviews

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.reviews.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.reviews.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.reviews.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        await self.reviews.purge_seller(seller_id)
