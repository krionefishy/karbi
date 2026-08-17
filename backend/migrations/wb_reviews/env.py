from backend.migrations.common import run_migrations
from backend.modules.wb_reviews.infrastructure.postgres.models import WBReviewsBase

run_migrations(WBReviewsBase.metadata, "wb_reviews")
