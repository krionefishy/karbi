import asyncio
import signal

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_core.infrastructure.wb import WBThrottle, budgets_for
from backend.modules.wb_turnover.infrastructure.wb import (
    MARKETPLACE_BUCKET,
    STATISTICS_BUCKET,
    WBMarketplaceClient,
    WBStatisticsClient,
)
from backend.shared.security import CredentialCipher
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.storage.redis import RedisClient
from backend.workers.wb_turnover.worker import TurnoverWorker


class TurnoverWorkerApplication:
    """Collects stock and orders on a schedule and hands alerts to notifications.

    No Kafka: a step here is a couple of requests per seller, and a missed slot
    is covered by the next one a few hours later. What must not be lost — the
    orders watermark and the digest — is protected by the database instead.
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
        throttle = self._create_throttle()
        self.worker = TurnoverWorker(
            self.database,
            cipher,
            WBStatisticsClient(throttle=throttle),
            WBMarketplaceClient(throttle=throttle),
            self.settings,
        )

    def _create_throttle(self) -> WBThrottle:
        wb_api = self.settings.wb_api
        return WBThrottle(
            budgets={
                **budgets_for(
                    STATISTICS_BUCKET,
                    per_key=wb_api.statistics_per_key,
                    per_host=wb_api.statistics_per_host,
                    window_seconds=wb_api.window_seconds,
                ),
                **budgets_for(
                    MARKETPLACE_BUCKET,
                    per_key=wb_api.marketplace_per_key,
                    per_host=wb_api.marketplace_per_host,
                    window_seconds=wb_api.window_seconds,
                ),
            },
            redis_client=self.redis,
            max_wait_seconds=wb_api.max_wait_seconds,
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
    application = TurnoverWorkerApplication()
    application.install_signal_handlers()
    await application.run()
