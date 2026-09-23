from backend.migrations.common import run_migrations
from backend.modules.wb_returns.infrastructure.postgres.models import WBReturnsBase

run_migrations(WBReturnsBase.metadata, "wb_returns")
