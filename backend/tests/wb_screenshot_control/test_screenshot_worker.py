import json
from dataclasses import replace
from typing import Any

from backend.shared.kafka_streams.topics import ScreenshotControlTopics, StorageTopics, all_topics
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.workers.wb_screenshot_control.consumers import EventConsumer
from backend.workers.wb_screenshot_control.main import ScreenshotControlWorkerApplication
from backend.workers.wb_screenshot_control.worker import ScreenshotControlWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


async def test_the_worker_loop_ticks_and_stops(monkeypatch) -> None:
    worker = ScreenshotControlWorker(Database(), SETTINGS)
    assert worker.poll_interval_seconds == SETTINGS.screenshot_control.poll_seconds

    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        worker.stop()

    monkeypatch.setattr(worker, "tick", tick)
    await worker.run()
    assert ticks == 1


async def test_an_idle_module_does_nothing() -> None:
    off = replace(SETTINGS, screenshot_control=replace(SETTINGS.screenshot_control, mode="off"))
    await ScreenshotControlWorker(Database(), off).tick()


def test_the_application_builds_the_worker_without_consumers_yet() -> None:
    application = ScreenshotControlWorkerApplication(SETTINGS)
    assert isinstance(application.worker, ScreenshotControlWorker)
    assert application.consumers() == {}


def test_with_kafka_each_pipeline_topic_gets_its_consumer() -> None:
    settings = replace(SETTINGS, kafka=replace(SETTINGS.kafka, enabled=True))
    application = ScreenshotControlWorkerApplication(settings)
    names = set(application.consumers())
    assert {"screenshot-extract", "screenshot-decide"} <= names


class Recorder:
    def __init__(self, fail: int = 0) -> None:
        self.fail = fail
        self.seen: list[dict[str, Any]] = []
        self.parked: list[tuple[dict[str, Any], str]] = []
        self.slept: list[float] = []

    async def handler(self, payload: dict[str, Any]) -> None:
        self.seen.append(payload)
        if payload.get("poison") or len(self.seen) <= self.fail:
            raise RuntimeError("boom")

    async def park(self, payload: dict[str, Any], error: str) -> None:
        self.parked.append((payload, error))

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def consumer(self, **kwargs: Any) -> EventConsumer:
        return EventConsumer(
            topic="t", bootstrap_servers="", group_id="g", handler=self.handler, sleep=self.sleep, **kwargs
        )


def raw(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


async def test_a_transient_failure_is_retried_on_the_same_offset() -> None:
    recorder = Recorder(fail=2)
    consumer = recorder.consumer(on_poison=recorder.park)
    message = raw({"event_id": "e1"})
    assert [await consumer.handle(message, position="0:5") for _ in range(3)] == [False, False, True]
    assert recorder.parked == [] and consumer.failures == {} and len(recorder.slept) == 2


async def test_a_poison_message_is_parked_after_the_limit_and_the_next_one_is_handled() -> None:
    recorder = Recorder()
    consumer = recorder.consumer(on_poison=recorder.park, max_attempts=3)
    poison = raw({"event_id": "bad", "poison": True, "submission_id": "s1"})
    assert [await consumer.handle(poison, position="0:7") for _ in range(3)] == [False, False, True]
    assert recorder.parked[0][0]["submission_id"] == "s1" and "RuntimeError: boom" in recorder.parked[0][1]
    assert await consumer.handle(raw({"event_id": "good"}), position="0:8") is True
    assert recorder.seen[-1] == {"event_id": "good"}


async def test_a_failing_park_does_not_block_the_partition() -> None:
    recorder = Recorder()

    async def broken(payload: dict[str, Any], error: str) -> None:
        raise ConnectionError("db down")

    consumer = recorder.consumer(on_poison=broken, max_attempts=1)
    assert await consumer.handle(raw({"event_id": "bad", "poison": True}), position="0:1") is True


async def test_unparsable_messages_are_skipped_without_retries() -> None:
    recorder = Recorder()
    consumer = recorder.consumer()
    assert await consumer.handle(b"not json", position="0:1") is True
    assert await consumer.handle(raw([1, 2]), position="0:2") is True  # type: ignore[arg-type]
    assert recorder.seen == [] and recorder.slept == []


async def test_attempts_without_event_id_are_counted_by_offset() -> None:
    recorder = Recorder()
    consumer = recorder.consumer(on_poison=recorder.park, max_attempts=2)
    assert await consumer.handle(raw({"poison": True}), position="1:3") is False
    assert consumer.failures == {"offset:1:3": 1}


def test_module_topics_are_registered_for_creation() -> None:
    topics = set(all_topics())
    assert {
        ScreenshotControlTopics.SUBMITTED,
        ScreenshotControlTopics.EXTRACTED,
        ScreenshotControlTopics.VERDICT_READY,
        StorageTopics.WB_SCREENSHOT_CONTROL_UPLOAD,
        StorageTopics.WB_SCREENSHOT_CONTROL_DELETE,
    } <= topics
    assert StorageTopics.WB_SCREENSHOT_CONTROL_UPLOAD == "storage.s3.wb-screenshot-control.upload"
