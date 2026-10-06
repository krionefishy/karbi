import asyncio
import signal

from backend.infrastructure.logging import configure_logging
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.fin_reports.worker import FinReportsWorker


class FinReportsWorkerApplication:
    """Отдельный процесс финансовых отчётов: складывает дочитанные зеркалом отчёты реализации.

    В WB не ходит — читает зеркало `wb_core`, поэтому ни шлюз, ни ключи ему не нужны.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.worker = FinReportsWorker(self.database, self.settings)

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, pool_size=2, max_overflow=2)
            await self.worker.run()
        finally:
            await self.database.disconnect()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_worker() -> None:
    application = FinReportsWorkerApplication()
    application.install_signal_handlers()
    await application.run()
