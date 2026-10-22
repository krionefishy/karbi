import asyncio

from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_screenshot_control.worker import ScreenshotControlWorker


class ScreenshotControlWorkerApplication(WorkerApplication[ScreenshotControlWorker]):
    """Отдельный процесс контроля скриншотов ДБС, ровно одна реплика.

    В S3 пишет и удаляет только через свои консьюмеры событий `storage.s3.wb-screenshot-control.*`;
    файлы до выгрузки лежат в общем с api томе staging.
    """

    pool_size = 3
    max_overflow = 3

    def build_worker(self) -> ScreenshotControlWorker:
        return ScreenshotControlWorker(self.database, self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(ScreenshotControlWorkerApplication))
