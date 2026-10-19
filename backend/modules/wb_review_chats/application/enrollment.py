from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_review_chats.infrastructure.postgres import ReviewChatsRepository

AUTOMATION_ID = "wb-review-chats"
TITLE = "Чаты после отзыва"
DESCRIPTION = (
    "Диалоги, которые WB открывает с покупателем после отзыва с низкой оценкой: сколько покупателей ответили "
    "на наше сообщение следом и сколько промолчали, в сравнении с диалогами без него. По дням, со списком "
    "диалогов и выгрузкой в Excel."
)


class ReviewChatsEnrollment(TrackedEnrollment[ReviewChatsRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
