import asyncio
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.modules.notifications.domain import Bot
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.notifications.application import NotificationsWorkerApplication
from backend.workers.wb_reviews.application import WBReviewsWorkerApplication
from backend.workers.wb_reviews.worker import WBReviewsWorker
from backend.workers.wb_turnover.application import TurnoverWorkerApplication


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


def test_a_crashed_consumer_is_restarted_by_the_supervisor() -> None:
    """A dead consumer task used to go unnoticed until the process restarted."""
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = WBReviewsWorkerApplication(settings)
    application.consumer_restart_delay_seconds = 0
    starts = []

    async def scenario() -> None:
        alive = asyncio.Event()

        async def flaky_consumer() -> None:
            starts.append(1)
            if len(starts) == 1:
                raise RuntimeError("CommitFailedError after a rebalance")
            alive.set()
            await asyncio.Event().wait()

        supervisor = asyncio.create_task(application._supervise("wb-review-consumer", flaky_consumer))
        await asyncio.wait_for(alive.wait(), timeout=5)
        supervisor.cancel()
        await asyncio.gather(supervisor, return_exceptions=True)

    asyncio.run(scenario())
    assert len(starts) == 2


def test_a_transient_database_error_does_not_kill_the_worker_loop() -> None:
    failing_worker = worker(0, 30)
    attempts = []

    async def failing_recover() -> None:
        attempts.append(1)
        failing_worker.stop()
        raise RuntimeError("database is down")

    failing_worker._recover = failing_recover  # type: ignore[method-assign]

    # The loop swallows the error and lives on to the next poll instead of dying.
    asyncio.run(failing_worker.run())
    assert attempts == [1]


def test_notifications_worker_starts_a_poller_for_every_registered_bot() -> None:
    """Bots come from the table, so adding one must not need a deploy."""
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = NotificationsWorkerApplication(settings)
    first, second = Bot(uuid.uuid4(), "alpha", "alpha_bot", ""), Bot(uuid.uuid4(), "beta", "beta_bot", "")
    application._pollers = {}

    async def scenario() -> None:
        # The supervisor reads the bot table once and drives the pollers from it.
        application._sync_pollers([])
        assert application._pollers == {}

        application._sync_pollers([(first, "token-1"), (second, "token-2")])
        assert set(application._pollers) == {first.id, second.id}

        # A bot switched off in the table loses its poller on the next sweep.
        application._sync_pollers([(first, "token-1")])
        assert set(application._pollers) == {first.id}

        for task in application._pollers.values():
            task.cancel()
        await asyncio.gather(*application._pollers.values(), return_exceptions=True)

    asyncio.run(scenario())


def test_the_turnover_worker_runs_each_step_after_its_moment_of_the_day() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    application = TurnoverWorkerApplication(settings)
    worker = application.worker

    assert worker.is_due(moment(3, 0), 3, 0) is True
    assert worker.is_due(moment(2, 59), 3, 0) is False
    # The digest waits for its own hour even though the maths ran at dawn.
    assert worker.is_due(moment(4, 0), settings.turnover.digest_hour, settings.turnover.digest_minute) is False
    assert worker.is_due(moment(10, 0), settings.turnover.digest_hour, settings.turnover.digest_minute) is True
