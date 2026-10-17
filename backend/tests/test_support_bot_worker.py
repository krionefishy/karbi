from datetime import UTC, datetime, time, timedelta

from backend.modules.wb_support_bot.application import ChatSender
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.wb_support_bot.worker import SupportBotWorker, policy_from

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


class NullSender:
    async def send(self, seller_id: str, reply_sign: str, text: str) -> None:
        raise AssertionError("отправка не ожидается")

    async def reply_signs(self, seller_id: str) -> dict[str, str]:
        return {}


def test_policy_follows_the_config() -> None:
    policy = policy_from(SETTINGS)

    assert policy.window.start == time(9) and policy.window.end == time(22)
    assert policy.rules.silence == timedelta(hours=4) and policy.rules.duration == timedelta(days=5)
    assert policy.pace.interval == timedelta(hours=1)
    assert policy.history > policy.rules.duration


async def test_the_worker_passes_through_sellers_only_every_tick_interval() -> None:
    clock = [datetime(2026, 10, 10, 6, 0, tzinfo=UTC)]
    sender: ChatSender = NullSender()
    worker = SupportBotWorker(Database(), sender, SETTINGS, now=lambda: clock[0])

    assert worker._due(clock[0])
    worker._last_tick = clock[0]
    clock[0] += timedelta(minutes=SETTINGS.support_bot.tick_minutes - 1)
    assert not worker._due(clock[0])
    clock[0] += timedelta(minutes=1)
    assert worker._due(clock[0])
