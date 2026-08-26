import asyncio
import signal

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient, marketplace_throttle
from backend.shared.security import CredentialCipher
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.storage.redis import RedisClient
from backend.workers.wb_fbs_distribution.worker import FbsDistributionWorker


class FbsDistributionWorkerApplication:
    """Отдельный процесс автоматизации распределения FBS.

    Свой воркер, а не шаг в оборачиваемости: та читает заявленные остатки, а
    этот ими управляет. Общими остаются HTTP-клиент, шифрование ключей и
    Redis-бюджет запросов к WB, но не жизненный цикл и не права.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.redis = RedisClient()
        cipher = CredentialCipher(
            self.settings.security.credential_encryption_keys,
            self.settings.security.credential_fingerprint_key,
        )
        throttle = marketplace_throttle(self.settings, self.redis)
        self.worker = FbsDistributionWorker(
            self.database,
            cipher,
            WBFbsMarketplaceClient(throttle=throttle),
            self.settings,
        )

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, pool_size=2, max_overflow=2)
            await self.redis.connect(self.settings.redis.url)
            await self.worker.run()
        finally:
            await self.redis.disconnect()
            await self.database.disconnect()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_worker() -> None:
    application = FbsDistributionWorkerApplication()
    application.install_signal_handlers()
    await application.run()
