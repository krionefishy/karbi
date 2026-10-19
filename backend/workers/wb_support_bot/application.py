import asyncio
import logging
import signal
from collections.abc import Callable, Coroutine

from backend.infrastructure.logging import configure_logging
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_support_bot.domain import MODE_OFF
from backend.modules.wb_support_bot.infrastructure.wb import WBChatSender
from backend.shared.kafka_streams.kafka import ensure_topics
from backend.shared.llm import LLMClient
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.workers.wb_support_bot.classify_consumer import ClassifyConsumer
from backend.workers.wb_support_bot.worker import SupportBotWorker


class SupportBotWorkerApplication:
    """Отдельный процесс бота: разбирает диалоги из зеркала чатов и отправляет напоминания.

    Рядом с основным циклом живёт консьюмер вердиктов: бот кладёт запрос в outbox,
    консьюмер спрашивает нейросеть и пишет ответ в таблицу вердиктов.
    """

    # Упавший консьюмер возвращается не сразу: жёсткий сбой не должен стать цикличным перезапуском.
    consumer_restart_delay_seconds: float = 5.0

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)
        self.logger = logging.getLogger("wb.support_bot.application")
        self.database = Database()
        self.worker = SupportBotWorker(self.database, WBChatSender(EgressGateway(self.settings.egress)), self.settings)
        self.consumer = self._create_consumer()

    def _create_consumer(self) -> ClassifyConsumer | None:
        if not self.settings.kafka.enabled or self.settings.support_bot.classifier_mode == MODE_OFF:
            return None
        return ClassifyConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.support-bot",
            LLMClient(self.settings.llm),
        )

    async def run(self) -> None:
        self.settings.validate_runtime_secrets()
        try:
            await self.database.connect(self.settings.database.url, pool_size=3, max_overflow=3)
            tasks: list[asyncio.Task[None]] = []
            if self.consumer is not None:
                await ensure_topics(
                    bootstrap_servers=self.settings.kafka.bootstrap_servers,
                    partitions=self.settings.kafka.topic_partitions,
                    replication_factor=self.settings.kafka.topic_replication_factor,
                )
                tasks = [
                    asyncio.create_task(
                        self._supervise("support-bot-classify", self.consumer.run), name="support-bot-classify"
                    )
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

    async def _supervise(self, name: str, factory: Callable[[], Coroutine[None, None, None]]) -> None:
        """Держит консьюмер живым: цикл заканчивается только исключением, и без присмотра топик замолчал бы."""
        while True:
            try:
                await factory()
                self.logger.error("consumer_exited_unexpectedly", extra={"consumer": name})
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("consumer_crashed", extra={"consumer": name})
            await asyncio.sleep(self.consumer_restart_delay_seconds)

    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for name in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(name, self.worker.stop)


async def run_worker() -> None:
    application = SupportBotWorkerApplication()
    application.install_signal_handlers()
    await application.run()
