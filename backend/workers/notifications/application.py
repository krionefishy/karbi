import asyncio
import signal
import uuid

import structlog

from backend.infrastructure.logging import configure_logging
from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.notifications.sender import NotificationSender


class NotificationsWorkerApplication:
    """One process: queueing from Kafka plus a delivery loop per bot.

    Polling for incoming messages is not here any more — it belongs to the relay,
    which is the only host that can reach the messenger at all.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.database = Database()
        self.client = RelayClient(self.settings.relay)
        self.sender = NotificationSender(
            self.database,
            self.client,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.notifications",
            delivery_interval_seconds=self.settings.telegram.delivery_interval_seconds,
            send_max_attempts=self.settings.telegram.send_max_attempts,
            send_retry_backoff_seconds=self.settings.telegram.send_retry_backoff_seconds,
        )
        self._deliveries: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._sender_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.logger = structlog.get_logger("notifications_worker")

    def stop(self) -> None:
        self._stop.set()

    async def supervise_bots(self) -> None:
        """Follow the bot table: a bot added today gets a delivery loop without a deploy."""
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                self._sync_sender()
                bots = await self._active_bots()
                self._sync_deliveries(bots)
            except Exception:
                self.logger.exception("bot_supervisor_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.telegram.bot_refresh_seconds)
            except TimeoutError:
                continue

    async def _active_bots(self) -> list[Bot]:
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            return await BotRegistry(session, repository).active()

    def _sync_sender(self) -> None:
        """Keep the Kafka consumer alive: a dead task is restarted, like a poller."""
        if not self.settings.kafka.enabled:
            return
        task = self._sender_task
        if task is not None and not task.done():
            return
        if task is not None and not task.cancelled() and task.exception() is not None:
            self.logger.error("notification_sender_died", error=str(task.exception()))
        self._sender_task = asyncio.create_task(self.sender.consume(), name="notification-sender")

    def _sync_deliveries(self, bots: list[Bot]) -> None:
        """A delivery loop per bot, kept in step with the bot table.

        A bot registered today starts sending within bot_refresh_seconds, with
        no deploy.
        """
        active_ids = {bot.id for bot in bots}
        for bot_id, task in list(self._deliveries.items()):
            if bot_id not in active_ids or task.done():
                task.cancel()
                del self._deliveries[bot_id]
        for bot in bots:
            if bot.id not in self._deliveries:
                self._deliveries[bot.id] = asyncio.create_task(
                    self.sender.deliver_forever(bot), name=f"notification-delivery-{bot.code}"
                )
                self.logger.info("bot_delivery_scheduled", bot=bot.code)

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        # A delivery loop per bot plus the Kafka consumer all want a session at
        # once; two connections were sized for a single delivery loop and would
        # hand out pool timeouts instead.
        await self.database.connect(self.settings.database.url, pool_size=5, max_overflow=10)
        try:
            if self.settings.kafka.enabled:
                await ensure_topics(
                    bootstrap_servers=self.settings.kafka.bootstrap_servers,
                    partitions=self.settings.kafka.topic_partitions,
                    replication_factor=self.settings.kafka.topic_replication_factor,
                )
            # Both the Kafka consumer and the per-bot delivery loops are started
            # (and restarted) by supervise_bots.
            await self.supervise_bots()
        finally:
            sender_tasks = [self._sender_task] if self._sender_task is not None else []
            running = [*sender_tasks, *self._deliveries.values()]
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            await self.database.disconnect()

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.stop)


async def run_worker() -> None:
    application = NotificationsWorkerApplication()
    application.install_signal_handlers()
    await application.run()
