import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import StockMirror


class StockSource(Protocol):
    """Остаток по nmID для фильтра уведомлений. Карточки нет в ответе — остаток неизвестен."""

    async def stock(self, seller_id: uuid.UUID, *, now: datetime) -> Mapping[int, int]: ...


class MirrorStockSource:
    """FBO + FBS из зеркала wb_core — то же число, что видит чек-лист карточек.

    Сами остатки в WB не собираем. Зеркало старше `fresh_hours` или ещё не собранное —
    пустой ответ: тогда остаток неизвестен у всех карточек и уведомления не подавляются.
    """

    def __init__(self, session: AsyncSession, *, fresh_hours: int) -> None:
        self.mirror = StockMirror(session)
        self.fresh = timedelta(hours=fresh_hours)

    async def stock(self, seller_id: uuid.UUID, *, now: datetime) -> Mapping[int, int]:
        facts = await self.mirror.stock(seller_id, fresh_since=now - self.fresh)
        if not facts:
            return {}
        return {int(article): fact.total for article, fact in facts.items() if article.isdigit()}
