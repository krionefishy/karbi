import asyncio
import signal

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_fbs_stocks.infrastructure.wb import WBFbsStocksClient
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.wb_fbs_stocks.worker import FbsStocksWorker


class FbsStocksWorkerApplication:
    """Отдельный процесс таблицы остатков FBS: опрашивает склады кабинетов раз в N минут."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.worker = FbsStocksWorker(
            self.database, WBFbsStocksClient(EgressGateway(self.settings.egress)), self.settings
        )

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
    application = FbsStocksWorkerApplication()
    application.install_signal_handlers()
    await application.run()
