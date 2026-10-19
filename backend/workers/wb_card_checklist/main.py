import asyncio

from backend.modules.wb_card_checklist.infrastructure.wb import WBCardClient, WBPricesClient
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_card_checklist.worker import CardChecklistWorker


class CardChecklistWorkerApplication(WorkerApplication[CardChecklistWorker]):
    """Отдельный процесс чек-листа карточек: раз в сутки перечитывает карточки и цены.

    Ключи и бюджет запросов к WB живут на шлюзе wb-egress; остатки и отзывы
    этот процесс не собирает — их он берёт у соседних автоматизаций.
    """

    def build_worker(self) -> CardChecklistWorker:
        gateway = EgressGateway(self.settings.egress)
        return CardChecklistWorker(self.database, WBCardClient(gateway), WBPricesClient(gateway), self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(CardChecklistWorkerApplication))
