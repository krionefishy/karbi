import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_review_chats.infrastructure.postgres import ReviewChatsRepository

AUTOMATION_ID = "wb-review-chats"
TITLE = "Чаты после отзыва"
DESCRIPTION = (
    "Диалоги, которые WB открывает с покупателем после отзыва с низкой оценкой: сколько покупателей ответили "
    "на наше сообщение следом и сколько промолчали, в сравнении с диалогами без него. По дням, со списком "
    "диалогов и выгрузкой в Excel."
)


class ReviewChatsEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, tracked: ReviewChatsRepository) -> None:
        self.tracked = tracked

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.tracked.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        await self.tracked.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.tracked.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        """Своей истории у модуля нет: события чатов принадлежат зеркалу и уходят вместе с селлером."""
        await self.tracked.untrack(seller_id)
