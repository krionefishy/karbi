from backend.migrations.common import run_migrations
from backend.modules.wb_review_chats.infrastructure.postgres.models import WBReviewChatsBase

run_migrations(WBReviewChatsBase.metadata, "wb_review_chats")
