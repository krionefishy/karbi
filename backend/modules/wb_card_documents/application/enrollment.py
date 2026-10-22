from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository
from backend.modules.wb_core.application import TrackedEnrollment

AUTOMATION_ID = "wb-card-documents"
TITLE = "Документы карточек WB"
DESCRIPTION = (
    "Следит за декларациями и сертификатами в карточках: отказ WB по документу, документ не загружен, "
    "срок истекает, номер или даты в характеристиках расходятся с документом. Сообщает в Telegram."
)


class CardDocumentsEnrollment(TrackedEnrollment[CardDocumentsRepository]):
    automation_id = AUTOMATION_ID
    title = TITLE
