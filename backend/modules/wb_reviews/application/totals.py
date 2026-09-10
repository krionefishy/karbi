import uuid

from backend.modules.wb_reviews.domain import ReviewTotals
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository


class ReviewTotalsReader:
    """Что автоматизация отзывов знает об отзывах селлера на последний срез.

    Дверь для соседних модулей: чек-листу карточки нужны те же числа, и
    полное сканирование отзывов ради них второй раз не повторяется. Читать
    таблицы этой схемы напрямую соседям нельзя — только через этот порт.
    """

    def __init__(self, reviews: ReviewSyncRepository) -> None:
        self.reviews = reviews

    async def totals(self, seller_id: uuid.UUID) -> dict[str, ReviewTotals] | None:
        """None — селлер не подключён к отзывам, и чисел взять неоткуда."""
        if not await self.reviews.is_tracked(seller_id):
            return None
        return await self.reviews.latest_totals(seller_id)
