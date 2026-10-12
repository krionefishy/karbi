from backend.migrations.common import run_migrations
from backend.modules.wb_support_bot.infrastructure.postgres.models import WBSupportBotBase

run_migrations(WBSupportBotBase.metadata, "wb_support_bot")
