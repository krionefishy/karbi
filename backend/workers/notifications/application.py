import asyncio
import signal
import uuid

import structlog

from backend.infrastructure.logging import configure_logging
from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import TelegramClient
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.security import CredentialCipher
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.notifications.poller import BotPoller
from backend.workers.notifications.sender import NotificationSender


class NotificationsWorkerApplication:
    """One process: a poller per registered bot, plus queueing and delivery.

    Run it in a single replica. Two replicas would each poll every bot, and
    Telegram refuses the second getUpdates on a token.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.cipher = CredentialCipher(
            self.settings.security.credential_encryption_keys,
            self.settings.security.credential_fingerprint_key,
        )
        self.client = TelegramClient(
            base_url=self.settings.telegram.api_base_url,
            request_timeout_seconds=self.settings.telegram.request_timeout_seconds,
            poll_timeout_seconds=self.settings.telegram.poll_timeout_seconds,
        )
        self.sender = NotificationSender(
            self.database,
            self.cipher,
            self.client,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.notifications",
            delivery_interval_seconds=self.settings.telegram.delivery_interval_seconds,
            send_max_attempts=self.settings.telegram.send_max_attempts,
            send_retry_backoff_seconds=self.settings.telegram.send_retry_backoff_seconds,
        )
        self._pollers: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stop = asyncio.Event()
        self.logger = structlog.get_logger("notifications_worker")

    def stop(self) -> None:
        self._stop.set()

    async def supervise_bots(self) -> None:
        """Follow the bot table: a bot added today starts polling without a deploy."""
        while not self._stop.is_set():
            try:
                await self._sync_pollers()
            except Exception:
                self.logger.exception("bot_supervisor_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.telegram.bot_refresh_seconds)
            except TimeoutError:
                continue

    async def _active_bots(self) -> list[tuple[Bot, str]]:
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            registry = BotRegistry(session, repository, self.cipher)
            return [(bot, await registry.token(bot.id)) for bot in await registry.active()]

    async def _sync_pollers(self) -> None:
        bots = await self._active_bots()
        active_ids = {bot.id for bot, _ in bots}
        for bot_id, task in list(self._pollers.items()):
            if bot_id not in active_ids or task.done():
                task.cancel()
                del self._pollers[bot_id]
        for bot, token in bots:
            if bot.id not in self._pollers:
                self._pollers[bot.id] = asyncio.create_task(self._poll(bot, token), name=f"telegram-poller-{bot.code}")
                self.logger.info("bot_poller_scheduled", bot=bot.code)

    async def _poll(self, bot: Bot, token: str) -> None:
        poller = BotPoller(self.database, self.client, bot, token, self.settings.telegram.invite_link_ttl_hours)
        await poller.run()

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        await self.database.connect(self.settings.database.url, pool_size=2, max_overflow=2)
        tasks: list[asyncio.Task[None]] = []
        try:
            if self.settings.kafka.enabled:
                await ensure_topics(
                    bootstrap_servers=self.settings.kafka.bootstrap_servers,
                    partitions=self.settings.kafka.topic_partitions,
                    replication_factor=self.settings.kafka.topic_replication_factor,
                )
                tasks.append(asyncio.create_task(self.sender.consume(), name="notification-sender"))
            tasks.append(asyncio.create_task(self.sender.deliver_forever(), name="notification-delivery"))
            await self.supervise_bots()
        finally:
            for task in [*tasks, *self._pollers.values()]:
                task.cancel()
            await asyncio.gather(*tasks, *self._pollers.values(), return_exceptions=True)
            await self.database.disconnect()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.stop)


async def run_worker() -> None:
    application = NotificationsWorkerApplication()
    application.install_signal_handlers()
    await application.run()
