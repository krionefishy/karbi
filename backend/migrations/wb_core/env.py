from backend.migrations.common import run_migrations
from backend.modules.wb_core.infrastructure.postgres.models import WBCoreBase

run_migrations(WBCoreBase.metadata, "wb_core")
