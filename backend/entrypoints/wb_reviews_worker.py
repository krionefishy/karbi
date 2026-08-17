import asyncio
import signal

import structlog

from backend.config import get_settings
from backend.infrastructure.database import Database
from backend.infrastructure.logging import configure_logging


class WBReviewsWorker:
    def __init__(self, database: Database, poll_interval_seconds: int = 30) -> None:
        self._database = database
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._logger = structlog.get_logger("wb_reviews_worker")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._logger.info("worker_started")
        while not self._stop.is_set():
            # Scheduling and WB synchronization will be implemented in the wb_reviews module.
            self._logger.debug("worker_poll")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
        self._logger.info("worker_stopped")


async def run_worker() -> None:
    settings = get_settings()
    configure_logging(settings.app_log_level)
    database = Database(settings.database_url)
    worker = WBReviewsWorker(database)

    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_name, worker.stop)

    try:
        await worker.run()
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(run_worker())
