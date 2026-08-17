import asyncio

import structlog


class WBReviewsWorker:
    """Scheduling loop. Use cases will be resolved by the worker application, not infrastructure-injected here."""

    def __init__(self, poll_interval_seconds: int) -> None:
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._logger = structlog.get_logger("wb_reviews_worker")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._logger.info("worker_started")
        while not self._stop.is_set():
            # The daily WB synchronization use case will be resolved from the DI scope here.
            self._logger.debug("worker_poll")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
        self._logger.info("worker_stopped")
