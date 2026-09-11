from backend.migrations.common import run_migrations
from backend.modules.wb_fbs_stocks.infrastructure.postgres.models import WBFbsStocksBase

run_migrations(WBFbsStocksBase.metadata, "wb_fbs_stocks")
