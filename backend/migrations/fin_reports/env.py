from backend.migrations.common import run_migrations
from backend.modules.fin_reports.infrastructure.postgres.models import FinReportsBase

run_migrations(FinReportsBase.metadata, "fin_reports")
