from datetime import datetime, timedelta

from backend.modules.wb_screenshot_control.application.verdicts import decide_system, reason
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import ScreenshotRepository
from backend.shared.settings.config import ScreenshotControlConfig
from backend.storage.pg import Database

BATCH = 200


async def expire_outcomes(database: Database, config: ScreenshotControlConfig, now: datetime) -> int:
    """Скрин без исхода дольше `outcome_ttl_hours` — отказ `no_outcome`, что бы ни решил конвейер."""
    cutoff = now - timedelta(hours=config.outcome_ttl_hours)
    async with database.session() as session:
        expired = await ScreenshotRepository(session).expired_without_outcome(cutoff, BATCH)
        for submission in expired:
            decide_system(session, submission, "rejected", [reason("no_outcome", "warn")], now=now)
        await session.commit()
    return len(expired)
