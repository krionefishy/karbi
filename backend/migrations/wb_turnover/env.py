from backend.migrations.common import run_migrations
from backend.modules.wb_turnover.infrastructure.postgres.models import WBTurnoverBase

run_migrations(WBTurnoverBase.metadata, "wb_turnover")
