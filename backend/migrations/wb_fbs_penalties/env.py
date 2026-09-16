from backend.migrations.common import run_migrations
from backend.modules.wb_fbs_penalties.infrastructure.postgres.models import WBFbsPenaltiesBase

run_migrations(WBFbsPenaltiesBase.metadata, "wb_fbs_penalties")
