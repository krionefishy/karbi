from dataclasses import replace

from backend.shared.kafka_streams.topics import ScreenshotControlTopics, StorageTopics, all_topics
from backend.shared.settings import load_settings
from backend.storage.pg import Database
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
