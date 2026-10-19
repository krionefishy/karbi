from backend.modules.wb_core.application import TrackedEnrollment
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository

AUTOMATION_ID = "wb-reviews"
TITLE = "Мониторинг отзывов Wildberries"
DESCRIPTION = "Ежедневные снимки отзывов по подключённым товарам и селлерам Wildberries."


class ReviewsEnrollment(TrackedEnrollment[ReviewSyncRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
