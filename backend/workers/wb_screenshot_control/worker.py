from backend.shared.settings import Settings
from backend.shared.worker import PollingWorker
from backend.storage.pg import Database


class ScreenshotControlWorker(PollingWorker):
    """Контроль скриншотов ДБС: основной цикл процесса.

    Этап 1 — каркас с heartbeat. Распознавание, решения, выгрузка и удаление в S3 приедут
    консьюмерами Kafka (отдельные группы), а чистка по сроку и сверка зависших объектов —
    шагами этого цикла.
    """

    tick_failed_event = "screenshot_control_tick_failed"

    def __init__(self, database: Database, settings: Settings) -> None:
        super().__init__(
            logger_name="wb_screenshot_control_worker",
            poll_interval_seconds=settings.screenshot_control.poll_seconds,
        )
        self.database = database
        self.settings = settings
        self.config = settings.screenshot_control

    async def tick(self) -> None:
        if self.config.mode == "off":
            return
