import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_checklist.application.ports import ReviewCounts
from backend.modules.wb_core.application import ReviewMirror, StockMirror


class MirrorStockSource:
    """Остаток из зеркала wb_core: FBO и FBS вместе, как один «есть ли товар».

    Зеркало собирает всех активных селлеров, подключение к другой автоматизации
    для этого не нужно. `None` — зеркало ещё не доходило до селлера; пусто —
    последний сбор старше `fresh_days`.
    """

    def __init__(self, session: AsyncSession, *, fresh_days: int) -> None:
        self.mirror = StockMirror(session)
        self.fresh_days = fresh_days

    async def stock(self, seller_id: uuid.UUID, today: date) -> dict[str, int] | None:
        fresh_since = datetime.combine(today - timedelta(days=self.fresh_days), datetime.min.time(), tzinfo=UTC)
        facts = await self.mirror.stock(seller_id, fresh_since=fresh_since)
        if facts is None:
            return None
        return {article: fact.total for article, fact in facts.items()}


class MirrorReviewSource:
    """Отзывы по карточкам из зеркала wb_core; `None` — ещё не собирали."""

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = ReviewMirror(session)

    async def totals(self, seller_id: uuid.UUID) -> Mapping[str, ReviewCounts] | None:
        return await self.mirror.totals(seller_id)
