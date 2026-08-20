import uuid
from datetime import UTC, date, datetime

from backend.app.api.utils import automation_catalog, next_run_at, next_turnover_run_at
from backend.modules.wb_reviews.application import SyncOverview
from backend.modules.wb_reviews.domain import ReviewSyncRun
from backend.modules.wb_turnover.application import TurnoverOverview
from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


def quiet_turnover() -> TurnoverOverview:
    """A turnover automation that has never run — the reviews card must not care."""
    return TurnoverOverview(seller_count=0, last_run=None, last_success_at=None, runs_last_24h=0)


def run(status: str, *, started: datetime | None = None, finished: datetime | None = None) -> ReviewSyncRun:
    return ReviewSyncRun(
        id=uuid.uuid4(),
        trigger="scheduled",
        snapshot_date=date(2026, 8, 18),
        status=status,
        total_sellers=4,
        completed_sellers=3,
        failed_sellers=1,
        created_at=datetime(2026, 8, 18, 9, 0, tzinfo=UTC),
        started_at=started,
        finished_at=finished,
        jobs=(),
    )


def test_catalog_reports_the_run_that_actually_happened() -> None:
    finished = datetime(2026, 8, 18, 9, 4, 30, tzinfo=UTC)
    overview = SyncOverview(
        seller_count=4,
        last_run=run("partial_success", started=datetime(2026, 8, 18, 9, 0, tzinfo=UTC), finished=finished),
        last_success_at=datetime(2026, 8, 17, 9, 2, tzinfo=UTC),
        runs_last_24h=2,
    )

    [automation, _] = automation_catalog(overview, quiet_turnover(), SETTINGS)

    assert automation.id == "wb-reviews"
    assert automation.seller_count == 4
    assert automation.runs_last_24h == 2
    assert automation.last_success_at == "2026-08-17T09:02:00+00:00"
    assert automation.last_run is not None
    assert automation.last_run.failed_sellers == 1
    assert automation.last_run.duration_seconds == 270


def test_status_follows_the_last_run() -> None:
    def status_for(last_run: ReviewSyncRun | None) -> str:
        return SyncOverview(seller_count=1, last_run=last_run, last_success_at=None, runs_last_24h=0).status

    assert status_for(None) == "idle"
    assert status_for(run("queued")) == "running"
    assert status_for(run("running")) == "running"
    assert status_for(run("success")) == "active"
    assert status_for(run("partial_success")) == "degraded"
    assert status_for(run("error")) == "failed"


def test_a_run_that_never_finished_has_no_duration() -> None:
    overview = SyncOverview(
        seller_count=1,
        last_run=run("running", started=datetime(2026, 8, 18, 9, 0, tzinfo=UTC)),
        last_success_at=None,
        runs_last_24h=1,
    )

    [automation, _] = automation_catalog(overview, quiet_turnover(), SETTINGS)

    assert automation.last_run is not None
    assert automation.last_run.duration_seconds is None
    assert automation.last_run.finished_at is None


def test_next_run_is_the_worker_schedule() -> None:
    # 00:30 Moscow is 21:30 UTC the day before, so a moment just after it must
    # roll the next run forward a day.
    expected = (SETTINGS.worker.review_sync_hour, SETTINGS.worker.review_sync_minute)

    before = next_run_at(SETTINGS, datetime(2026, 8, 18, 20, 0, tzinfo=UTC))
    assert (before.hour, before.minute, before.day) == (*expected, 19)

    after = next_run_at(SETTINGS, datetime(2026, 8, 18, 22, 0, tzinfo=UTC))
    assert (after.hour, after.minute, after.day) == (*expected, 20)


def test_the_catalog_lists_both_automations() -> None:
    turnover = TurnoverOverview(seller_count=2, last_run=None, last_success_at=None, runs_last_24h=0)

    reviews_card, turnover_card = automation_catalog(
        SyncOverview(seller_count=4, last_run=None, last_success_at=None, runs_last_24h=0), turnover, SETTINGS
    )

    assert (reviews_card.id, turnover_card.id) == ("wb-reviews", "wb-turnover")
    assert turnover_card.seller_count == 2
    assert turnover_card.status == "idle"


def test_the_turnover_card_points_at_its_nearest_daily_step() -> None:
    """The automation wakes several times a day; the card shows the closest one."""
    slots = sorted(SETTINGS.turnover.stock_slot_hours)

    after_midnight = next_turnover_run_at(SETTINGS, datetime(2026, 8, 18, 0, 10, tzinfo=UTC))
    assert after_midnight.hour == slots[0]

    # Just past the last slot of the day the next step rolls over to tomorrow.
    late = next_turnover_run_at(SETTINGS, datetime(2026, 8, 18, 21, 30, tzinfo=UTC))
    assert late.day == 19
