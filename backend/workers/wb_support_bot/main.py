import asyncio
from collections.abc import Awaitable, Callable

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_support_bot.domain import MODE_OFF
from backend.modules.wb_support_bot.infrastructure.wb import WBChatSender
from backend.shared.llm import LLMClient
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_support_bot.classify_consumer import ClassifyConsumer
from backend.workers.wb_support_bot.reply_consumer import ReplyConsumer
from backend.workers.wb_support_bot.worker import SupportBotWorker


class SupportBotWorkerApplication(WorkerApplication[SupportBotWorker]):
    """Отдельный процесс бота: разбирает диалоги из зеркала чатов и отправляет напоминания.

    Рядом с основным циклом живёт консьюмер вердиктов: бот кладёт запрос в outbox,
    консьюмер спрашивает нейросеть и пишет ответ в таблицу вердиктов.
    """

    pool_size = 3
    max_overflow = 3

    def build_worker(self) -> SupportBotWorker:
        self.consumer = self._create_consumer()
        self.reply_consumer = self._create_reply_consumer()
        return SupportBotWorker(self.database, WBChatSender(EgressGateway(self.settings.egress)), self.settings)

    def _create_consumer(self) -> ClassifyConsumer | None:
        if not self.settings.kafka.enabled or self.settings.support_bot.classifier_mode == MODE_OFF:
            return None
        return ClassifyConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.support-bot",
            LLMClient(self.settings.llm),
        )

    def _create_reply_consumer(self) -> ReplyConsumer | None:
        if not self.settings.kafka.enabled or self.settings.support_bot.reply_gate_mode == MODE_OFF:
            return None
        return ReplyConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.support-bot.reply",
            LLMClient(self.settings.llm),
        )

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        running: dict[str, Callable[[], Awaitable[None]]] = {}
        if self.consumer is not None:
            running["support-bot-classify"] = self.consumer.run
        if self.reply_consumer is not None:
            running["support-bot-reply"] = self.reply_consumer.run
        return running


if __name__ == "__main__":
    asyncio.run(run_application(SupportBotWorkerApplication))
