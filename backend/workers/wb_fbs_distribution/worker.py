import asyncio

import structlog

from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


class FbsDistributionWorker:
    """Расписание автоматизации: сверка складов, расчёт, публикация.

    Пока пусто: каркас поднимается вместе с модулем, чтобы развёртывание,
    healthcheck и остановка проверялись до того, как в цикл попадёт первый
    запрос к WB.
    """

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.logger = structlog.get_logger("wb_fbs_distribution_worker")
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.tick()
            except Exception:
                self.logger.exception("fbs_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        return None
