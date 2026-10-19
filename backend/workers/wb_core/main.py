import asyncio
from collections.abc import Awaitable, Callable
from datetime import date

from backend.modules.wb_core.application import MirrorService
from backend.modules.wb_core.infrastructure.wb import (
    EgressGateway,
    OzonFinanceClient,
    WBAdvertClient,
    WBAnalyticsClient,
    WBChatClient,
    WBContentClient,
    WBFeedbackClient,
    WBMarketplaceClient,
    WBSalesReportsClient,
    WBWarehouseRemainsClient,
)
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_core.catalog_consumer import CatalogSyncConsumer
from backend.workers.wb_core.worker import WBCoreWorker


class WBCoreWorkerApplication(WorkerApplication[WBCoreWorker]):
    """Отдельный процесс зеркала WB: расписание по всем селлерам плюс консьюмер каталога.

    Единственная точка, откуда наполняются каталог, остатки и отзывы в wb_core;
    автоматизации их только читают. Поэтому у процесса нет другой работы, а
    консьюмер прикрыт своим супервизором: heartbeat ловит только внешний цикл.
    """

    def build_worker(self) -> WBCoreWorker:
        gateway = EgressGateway(self.settings.egress)
        self.mirror = MirrorService(
            self.database,
            content=WBContentClient(gateway),
            analytics=WBAnalyticsClient(gateway),
            marketplace=WBMarketplaceClient(gateway),
            feedbacks=WBFeedbackClient(gateway, page_size=self.settings.worker.feedback_page_size),
            chats=WBChatClient(gateway),
            remains=WBWarehouseRemainsClient(gateway),
            sales_reports=WBSalesReportsClient(gateway),
            adverts=WBAdvertClient(gateway),
            ozon_finance=OzonFinanceClient(gateway),
            orders_history_months=self.settings.core_mirror.orders_history_months,
            chats_history_days=self.settings.core_mirror.chats_history_days,
            chats_pages_per_run=self.settings.core_mirror.chats_pages_per_run,
            sales_reports_history_from=date.fromisoformat(self.settings.core_mirror.sales_reports_history_from),
            sales_reports_per_run=self.settings.core_mirror.sales_reports_per_run,
            adverts_history_from=date.fromisoformat(self.settings.core_mirror.sales_reports_history_from),
            adverts_windows_per_run=self.settings.core_mirror.adverts_windows_per_run,
            adverts_overlap_days=self.settings.core_mirror.adverts_overlap_days,
            ozon_history_from=date.fromisoformat(self.settings.core_mirror.sales_reports_history_from),
            ozon_days_per_run=self.settings.core_mirror.ozon_days_per_run,
            ozon_overlap_days=self.settings.core_mirror.ozon_overlap_days,
            heartbeat=touch_heartbeat,
        )
        self.catalog_consumer = self._create_consumer()
        return WBCoreWorker(self.database, self.mirror, self.settings)

    def _create_consumer(self) -> CatalogSyncConsumer | None:
        if not self.settings.kafka.enabled:
            return None
        return CatalogSyncConsumer(
            self.database,
            self.settings.kafka.bootstrap_servers,
            f"{self.settings.kafka.consumer_group}.wb.catalog",
            mirror=self.mirror,
        )

    def consumers(self) -> dict[str, Callable[[], Awaitable[None]]]:
        if self.catalog_consumer is None:
            return {}
        return {"wb-catalog-consumer": self.catalog_consumer.run}


if __name__ == "__main__":
    asyncio.run(run_application(WBCoreWorkerApplication))
