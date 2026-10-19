import asyncio
from abc import ABC, abstractmethod

import structlog

from backend.shared.heartbeat import touch_heartbeat


class PollingWorker(ABC):
    """Цикл «проснуться, сделать шаг, уснуть» с heartbeat и мягкой остановкой.

    Расписание, слоты и сами шаги — дело наследника: он реализует `tick`. Ошибка шага
    логируется под `tick_failed_event` и не останавливает цикл.
    """

    tick_failed_event: str = "tick_failed"

    def __init__(self, *, logger_name: str, poll_interval_seconds: float) -> None:
        self.logger = structlog.get_logger(logger_name)
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    @abstractmethod
    async def tick(self) -> object:
        """Один проход: проверить, что пора, и сделать."""

    async def before_tick(self) -> None:
        """Обслуживание перед проходом; ошибка здесь пропускает проход, как и ошибка самого шага."""
        return None

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.before_tick()
                await self.tick()
            except Exception:
                self.logger.exception(self.tick_failed_event)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")
