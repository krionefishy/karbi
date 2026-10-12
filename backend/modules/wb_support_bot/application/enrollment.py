import uuid

from backend.modules.wb_core.application import AutomationEnrollment
from backend.modules.wb_support_bot.infrastructure.postgres import SupportBotRepository

AUTOMATION_ID = "wb-support-bot"
TITLE = "Бот поддержки в чатах"
DESCRIPTION = (
    "Пишет покупателю, который ответил в чате, получил ответ нашей стороны и замолчал: напоминания с "
    "9:00 до 22:00 по Москве в течение нескольких дней. Остановка на любой ответ покупателя. Режим "
    "«сухой прогон», журнал отправок, ссылки на диалоги, которые ждут специалиста."
)


class SupportBotEnrollment(AutomationEnrollment):
    automation_id = AUTOMATION_ID
    title = TITLE

    def __init__(self, repository: SupportBotRepository) -> None:
        self.repository = repository

    async def seller_ids(self) -> set[uuid.UUID]:
        return await self.repository.tracked_seller_ids()

    async def attach(self, seller_id: uuid.UUID) -> None:
        """Подключение не включает отправку: кабинет стартует в режиме сухого прогона."""
        await self.repository.track(seller_id)

    async def detach(self, seller_id: uuid.UUID) -> None:
        await self.repository.untrack(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        """Журнал отправок и диалоги принадлежат модулю и стираются вместе с кабинетом."""
        await self.repository.purge(seller_id)
