import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from backend.modules.notifications.domain import Bot
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.notifications.application import NotificationsWorkerApplication
from backend.workers.wb_reviews.application import WBReviewsWorkerApplication
from backend.workers.wb_reviews.worker import WBReviewsWorker


def test_worker_application_can_be_created() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")

    application = WBReviewsWorkerApplication(settings)

    assert application.settings is settings
    assert application.catalog_consumer is None
    assert application.review_consumer is None


def worker(hour: int, minute: int) -> WBReviewsWorker:
    return WBReviewsWorker(
        Database(),
        poll_interval_seconds=1,
        sync_hour=hour,
        sync_minute=minute,
        timezone="Europe/Moscow",
        enabled=True,
    )


def moment(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 18, hour, minute, tzinfo=ZoneInfo("Europe/Moscow"))


def test_a_midnight_schedule_waits_for_its_minute() -> None:
    # An hour-only comparison never held for hour 0, so a 00:30 run either
    # fired at midnight or, with the old guard, on every single poll.
    half_past_midnight = worker(0, 30)

    assert half_past_midnight.is_due(moment(0, 30)) is True
    assert half_past_midnight.is_due(moment(0, 31)) is True
    assert half_past_midnight.is_due(moment(23, 59)) is True
    assert half_past_midnight.is_due(moment(0, 29)) is False
    assert half_past_midnight.is_due(moment(0, 0)) is False


def test_a_daytime_schedule_still_waits_for_its_hour() -> None:
    noon = worker(12, 0)

    assert noon.is_due(moment(12, 0)) is True
    assert noon.is_due(moment(11, 59)) is False


def test_notifications_worker_starts_a_poller_for_every_registered_bot() -> None:
    """Bots come from the table, so adding one must not need a deploy."""
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = NotificationsWorkerApplication(settings)
    first, second = Bot(uuid.uuid4(), "alpha", "alpha_bot", ""), Bot(uuid.uuid4(), "beta", "beta_bot", "")
    application._pollers = {}

    async def scenario() -> None:
        with patch.object(NotificationsWorkerApplication, "_active_bots", AsyncMock(return_value=[])) as bots:
            await application._sync_pollers()
            assert application._pollers == {}

            bots.return_value = [(first, "token-1"), (second, "token-2")]
            await application._sync_pollers()
            assert set(application._pollers) == {first.id, second.id}

            # A bot switched off in the table loses its poller on the next sweep.
            bots.return_value = [(first, "token-1")]
            await application._sync_pollers()
            assert set(application._pollers) == {first.id}

        for task in application._pollers.values():
            task.cancel()
        await asyncio.gather(*application._pollers.values(), return_exceptions=True)

    asyncio.run(scenario())
