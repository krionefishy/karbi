from backend.migrations.common import run_migrations
from backend.modules.wb_card_documents.infrastructure.postgres.models import WBCardDocumentsBase

run_migrations(WBCardDocumentsBase.metadata, "wb_card_documents")
