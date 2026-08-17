from backend.migrations.common import run_migrations
from backend.modules.platform.infrastructure.postgres.models import PlatformBase

run_migrations(PlatformBase.metadata, "platform")
