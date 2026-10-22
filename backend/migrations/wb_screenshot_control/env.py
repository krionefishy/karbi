from backend.migrations.common import run_migrations
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import WBScreenshotControlBase

run_migrations(WBScreenshotControlBase.metadata, "wb_screenshot_control")
