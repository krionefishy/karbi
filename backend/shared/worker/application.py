import asyncio
import signal
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from backend.infrastructure.logging import configure_logging
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.settings import Settings, load_settings
from backend.shared.worker.polling import PollingWorker
from backend.storage.pg import Database


class WorkerApplication[W: PollingWorker](ABC):
    """Процесс воркера: настройки, база, основной цикл и консьюмеры Kafka рядом с ним.

    Наследник собирает воркер в `build_worker` и при необходимости перечисляет консьюмеров в
    `consumers`. Топики создаются внутри присмотра за консьюмером, а не до старта: недоступный
    Kafka не должен останавливать расписание, которому Kafka не нужен.
    """

    pool_size: int = 2
    max_overflow: int = 2
    consumer_restart_delay_seconds: float = 5.0

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.logger = structlog.get_logger(type(self).__name__)
        self.database = Database()
        self.worker: W = self.build_worker()

    @abstractmethod
    def build_worker(self) -> W:
        """Основной цикл процесса."""

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        """Фоновые консьюмеры: имя и корутина-цикл. По умолчанию их нет."""
        return {}

    def connect_options(self) -> dict[str, Any]:
        return {"pool_size": self.pool_size, "max_overflow": self.max_overflow}

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, **self.connect_options())
            tasks = [
                asyncio.create_task(self._supervise(name, self._with_topics(factory)), name=name)
                for name, factory in self.consumers().items()
            ]
            try:
                await self.worker.run()
            finally:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await self.database.disconnect()

    def _with_topics(self, factory: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
        async def run_consumer() -> None:
            await ensure_topics(
                bootstrap_servers=self.settings.kafka.bootstrap_servers,
                partitions=self.settings.kafka.topic_partitions,
                replication_factor=self.settings.kafka.topic_replication_factor,
            )
            await factory()

        return run_consumer

    async def _supervise(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        """Держит консьюмер живым: цикл заканчивается только исключением, и без присмотра топик замолчал бы."""
        while True:
            try:
                await factory()
                self.logger.error("consumer_exited_unexpectedly", consumer=name)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("consumer_crashed", consumer=name)
            await asyncio.sleep(self.consumer_restart_delay_seconds)

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_application(application_class: type[WorkerApplication[Any]]) -> None:
    application = application_class()
    application.install_signal_handlers()
    await application.run()
