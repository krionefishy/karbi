import asyncio
import dataclasses
import json
import logging
import uuid

import pytest

from backend.infrastructure.logging import configure_logging
from backend.modules.notifications.application import SendPacer
from backend.modules.notifications.domain import Bot
from backend.shared.settings import load_settings
from backend.workers.notifications.application import NotificationsWorkerApplication
from backend.workers.notifications.sender import NotificationSender

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


def sender(database: object = None) -> NotificationSender:
    return NotificationSender(
        database,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        "kafka.invalid:9092",
        "test-group",
        delivery_interval_seconds=0.01,
        send_max_attempts=3,
        send_retry_backoff_seconds=1,
    )


def test_httpx_loggers_do_not_log_token_urls() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)

    configure_logging("INFO")

    # httpx puts the full request URL — token included — into INFO records.
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


async def test_a_poisonous_kafka_message_is_rejected_not_raised(caplog) -> None:
    poisonous = [
        b"\xffnot json at all",
        b'["a", "json", "array"]',
        json.dumps({"message_id": "m1", "bot": "b"}).encode(),  # no template
    ]

    with caplog.at_level(logging.ERROR, logger="notifications.sender"):
        for raw in poisonous:
            await sender().process(raw)

    assert caplog.text.count("notification_payload_rejected") == len(poisonous)


async def test_an_infrastructure_error_still_propagates_for_retry() -> None:
    class DownDatabase:
        def session(self):
            raise RuntimeError("database is down")

    payload = json.dumps(
        {
            "message_id": "m1",
            "bot": "turnover",
            "template": "subscription.confirmed",
            "audience": {"type": "chat", "chat_id": 5},
        }
    ).encode()

    with pytest.raises(RuntimeError):
        await sender(DownDatabase()).process(payload)


async def test_a_dead_sender_task_is_restarted_by_the_supervisor() -> None:
    settings = dataclasses.replace(SETTINGS, kafka=dataclasses.replace(SETTINGS.kafka, enabled=True))
    application = NotificationsWorkerApplication(settings)
    crashes = 0

    async def crashing_consume() -> None:
        nonlocal crashes
        crashes += 1
        raise RuntimeError("kafka went away")

    application.sender.consume = crashing_consume  # type: ignore[method-assign]

    application._sync_sender()
    first = application._sender_task
    assert first is not None
    await asyncio.sleep(0)
    assert first.done() and crashes == 1

    application._sync_sender()
    second = application._sender_task
    assert second is not None and second is not first
    await asyncio.sleep(0)
    assert crashes == 2
    assert isinstance(second.exception(), RuntimeError)


def bot(code: str = "turnover") -> Bot:
    return Bot(id=uuid.uuid4(), code=code, username=f"{code}_bot", title=code)


async def test_the_pacer_keeps_a_full_second_between_two_sends_to_one_chat() -> None:
    now = 1000.0
    slept: list[float] = []

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        # Advance the clock the way a real event loop would.
        nonlocal now
        slept.append(seconds)
        now += seconds

    pacer = SendPacer(bot_messages_per_second=30.0, chat_messages_per_second=1.0, clock=clock, sleep=sleep)

    assert await pacer.wait_turn(1) == 0  # first send goes immediately
    # Same chat again: Telegram allows one message a second into a chat.
    assert await pacer.wait_turn(1) == pytest.approx(1.0)
    # A different chat is bound only by the bot-wide 30/s budget.
    assert await pacer.wait_turn(2) == pytest.approx(1 / 30, abs=1e-6)
    assert slept == pytest.approx([1.0, 1 / 30], abs=1e-6)


async def test_the_pacer_forgets_chats_it_has_not_seen_in_a_long_time() -> None:
    now = 0.0

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        return None

    pacer = SendPacer(clock=clock, sleep=sleep)
    await pacer.wait_turn(1)
    now = 10_000.0
    await pacer.wait_turn(2)

    assert 1 not in pacer._last_by_chat  # would grow without bound otherwise


async def test_a_hanging_bot_does_not_delay_another_bots_queue() -> None:
    """The whole point of a delivery loop per bot."""
    slow, quick = bot("slow"), bot("quick")
    started = asyncio.Event()
    release = asyncio.Event()
    delivered: list[str] = []

    class Deliveries:
        async def deliver_forever(self, which: Bot) -> None:
            if which.code == "slow":
                started.set()
                await release.wait()  # Telegram never answers
            delivered.append(which.code)

    application = NotificationsWorkerApplication(SETTINGS)
    application.sender = Deliveries()  # type: ignore[assignment]

    application._sync_deliveries([(slow, "token"), (quick, "token")])
    await started.wait()
    await asyncio.sleep(0)

    # The quick bot finished while the slow one is still hanging.
    assert delivered == ["quick"]
    assert not application._deliveries[slow.id].done()

    release.set()
    await asyncio.gather(*application._deliveries.values(), return_exceptions=True)


async def test_delivery_loops_follow_the_bot_table() -> None:
    first, second = bot("first"), bot("second")

    class Idle:
        async def deliver_forever(self, which: Bot) -> None:
            await asyncio.Event().wait()

    application = NotificationsWorkerApplication(SETTINGS)
    application.sender = Idle()  # type: ignore[assignment]

    application._sync_deliveries([(first, "token")])
    assert set(application._deliveries) == {first.id}

    # A bot registered since the last sweep starts delivering without a deploy;
    # one deactivated since then has its loop cancelled.
    application._sync_deliveries([(second, "token")])
    assert set(application._deliveries) == {second.id}

    for task in application._deliveries.values():
        task.cancel()
    await asyncio.gather(*application._deliveries.values(), return_exceptions=True)
