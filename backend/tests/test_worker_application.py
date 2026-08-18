from backend.shared.settings import load_settings
from backend.workers.wb_reviews.application import WBReviewsWorkerApplication


def test_worker_application_can_be_created() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    application = WBReviewsWorkerApplication(settings)

    assert application.settings is settings
    assert application.catalog_consumer is None
    assert application.review_consumer is None
