from backend.migrations.common import run_migrations
from backend.modules.notifications.infrastructure.postgres.models import NotificationsBase

run_migrations(NotificationsBase.metadata, "notifications")
