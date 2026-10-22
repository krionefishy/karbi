import asyncio

from backend.modules.wb_card_documents.infrastructure.wb import WBCardDocumentsClient
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.shared.worker import WorkerApplication, run_application
from backend.workers.wb_card_documents.worker import CardDocumentsWorker


class CardDocumentsWorkerApplication(WorkerApplication[CardDocumentsWorker]):
    """Отдельный процесс документов карточек: вердикты WB по декларациям и сообщения о переходах.

    Карточки читает сам через шлюз wb-egress; в мессенджер не ходит — только outbox.
    """

    def build_worker(self) -> CardDocumentsWorker:
        gateway = EgressGateway(self.settings.egress)
        return CardDocumentsWorker(self.database, WBCardDocumentsClient(gateway), self.settings)


if __name__ == "__main__":
    asyncio.run(run_application(CardDocumentsWorkerApplication))
