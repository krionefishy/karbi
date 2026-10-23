import asyncio
from collections.abc import Awaitable, Callable

from backend.shared.kafka_streams.topics import ScreenshotControlTopics, StorageTopics
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_screenshot_control.consumers import EventConsumer, Handler, PoisonHandler
from backend.workers.wb_screenshot_control.worker import ScreenshotControlWorker


class ScreenshotControlWorkerApplication(WorkerApplication[ScreenshotControlWorker]):
    """Отдельный процесс контроля скриншотов ДБС, ровно одна реплика.

    Консьюмеры: распознавание (`submitted`), решение (`extracted`, один ключ партиции) и, если S3
    настроен, выгрузка и удаление объектов (`storage.s3.wb-screenshot-control.*`). Без S3 подписчики
    хранилища не стартуют: файлы копятся в staging, приём и конвейер работают, сверка выгрузит их,
    когда S3 появится. Распознавание и решение собираются швами `wiring.build_extractor/build_decider`.
    """

    pool_size = 3
    max_overflow = 3

    def build_worker(self) -> ScreenshotControlWorker:
        return ScreenshotControlWorker(self.database, self.settings)

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        if not self.settings.kafka.enabled or self.settings.screenshot_control.mode == "off":
            return {}
        services = self.worker.services
        group = f"{self.settings.kafka.consumer_group}.wb.screenshot-control"
        # Ядовитое сообщение конвейера переводит скрин на проверку; у хранилища повтор сделает сверка.
        handlers: dict[str, tuple[str, Handler, PoisonHandler | None]] = {
            "screenshot-extract": (ScreenshotControlTopics.SUBMITTED, services.extraction().handle, services.park),
            "screenshot-decide": (ScreenshotControlTopics.EXTRACTED, services.decision().handle, services.park),
        }
        storage = services.object_storage()
        if storage is not None:
            handlers["screenshot-s3-upload"] = (StorageTopics.WB_SCREENSHOT_CONTROL_UPLOAD, storage.upload, None)
            handlers["screenshot-s3-delete"] = (StorageTopics.WB_SCREENSHOT_CONTROL_DELETE, storage.delete, None)
        return {
            name: EventConsumer(
                topic=topic,
                bootstrap_servers=self.settings.kafka.bootstrap_servers,
                group_id=f"{group}.{name}",
                handler=handler,
                on_poison=on_poison,
            ).run
            for name, (topic, handler, on_poison) in handlers.items()
        }


if __name__ == "__main__":
    asyncio.run(run_application(ScreenshotControlWorkerApplication))
