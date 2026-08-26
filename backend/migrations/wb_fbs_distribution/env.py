from backend.migrations.common import run_migrations
from backend.modules.wb_fbs_distribution.infrastructure.postgres.models import WBFbsDistributionBase

run_migrations(WBFbsDistributionBase.metadata, "wb_fbs_distribution")
