from backend.migrations.common import run_migrations
from backend.modules.wb_card_checklist.infrastructure.postgres.models import WBCardChecklistBase

run_migrations(WBCardChecklistBase.metadata, "wb_card_checklist")
