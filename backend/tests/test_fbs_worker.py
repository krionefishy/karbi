from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.egress_stub import make_gateway
from backend.workers.wb_fbs_distribution.worker import FbsDistributionWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
MOSCOW = ZoneInfo(SETTINGS.fbs_distribution.timezone)


def worker() -> FbsDistributionWorker:
    return FbsDistributionWorker(Database(), WBFbsMarketplaceClient(make_gateway()), SETTINGS)


def test_before_the_hour_the_mirror_is_measured_against_yesterday() -> None:
    """Otherwise a restart just after midnight would resync every cabinet a
    second time on the same day."""
    config = SETTINGS.fbs_distribution
    now = datetime(2026, 8, 20, config.mirror_hour, tzinfo=MOSCOW).replace(minute=0)
    before_the_hour = now.replace(hour=max(config.mirror_hour - 1, 0), minute=0)

    due = worker().due_since(before_the_hour).astimezone(MOSCOW)

    assert due.day == 19
    assert (due.hour, due.minute) == (config.mirror_hour, config.mirror_minute)


def test_after_the_hour_today_is_the_one_that_counts() -> None:
    config = SETTINGS.fbs_distribution
    after = datetime(2026, 8, 20, 23, 0, tzinfo=MOSCOW)

    due = worker().due_since(after).astimezone(MOSCOW)

    assert due.day == 20
    assert (due.hour, due.minute) == (config.mirror_hour, config.mirror_minute)


def test_the_moment_is_compared_in_utc() -> None:
    """Timestamps in the database are UTC; the schedule is Moscow. Mixing the
    two would move the sync by three hours twice a year."""
    due = worker().due_since(datetime(2026, 8, 20, 23, 0, tzinfo=MOSCOW))

    assert due.tzinfo is UTC
