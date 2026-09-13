import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import MIRROR_REVIEWS, MIRROR_STOCKS, ReviewFact, StockFact
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository


class StockMirror:
    """Что автоматизации читают из зеркала остатков.

    `None` — зеркало этого селлера ещё не собирало; пустой словарь — собирало,
    но данные старше `fresh_since`. Потребитель различает «ещё не было» и
    «устарело», чтобы сказать оператору правду, а не показывать нули.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def stock(self, seller_id: uuid.UUID, *, fresh_since: datetime) -> dict[str, StockFact] | None:
        state = await self.mirror.state(seller_id, MIRROR_STOCKS)
        if state is None or state.collected_at is None:
            return None
        if state.collected_at < fresh_since:
            return {}
        return await self.mirror.stocks(seller_id)

    async def collected_at(self, seller_id: uuid.UUID) -> datetime | None:
        state = await self.mirror.state(seller_id, MIRROR_STOCKS)
        return state.collected_at if state else None


class ReviewMirror:
    """Отзывы по карточкам из зеркала; `None` — ещё не собирали."""

    def __init__(self, session: AsyncSession) -> None:
        self.mirror = MirrorRepository(session)

    async def totals(self, seller_id: uuid.UUID) -> dict[str, ReviewFact] | None:
        state = await self.mirror.state(seller_id, MIRROR_REVIEWS)
        if state is None or state.collected_at is None:
            return None
        return await self.mirror.reviews(seller_id)

    async def collected_at(self, seller_id: uuid.UUID) -> datetime | None:
        state = await self.mirror.state(seller_id, MIRROR_REVIEWS)
        return state.collected_at if state else None
