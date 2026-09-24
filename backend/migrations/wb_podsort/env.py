from backend.migrations.common import run_migrations
from backend.modules.wb_podsort.infrastructure.postgres.models import WBPodsortBase

run_migrations(WBPodsortBase.metadata, "wb_podsort")
