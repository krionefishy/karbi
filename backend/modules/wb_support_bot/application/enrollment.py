from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_support_bot.infrastructure.postgres import SupportBotRepository

AUTOMATION_ID = "wb-support-bot"
TITLE = "Бот поддержки в чатах"
DESCRIPTION = (
    "Пишет покупателю, который ответил в чате, получил ответ нашей стороны и замолчал: напоминания с "
    "9:00 до 22:00 по Москве в течение нескольких дней. Смысл ответа покупателя оценивает нейросеть: "
    "рассылка продолжается или останавливается по классу ответа. Режим «сухой прогон», журнал "
    "отправок, ссылки на диалоги, которые ждут специалиста."
)


class SupportBotEnrollment(TrackedEnrollment[SupportBotRepository]):
    """Подключение не включает отправку: кабинет стартует в режиме сухого прогона.

    Журнал отправок и диалоги принадлежат модулю и стираются вместе с кабинетом.
    """

    automation_id = AUTOMATION_ID
    title = TITLE
