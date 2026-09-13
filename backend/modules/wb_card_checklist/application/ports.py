import uuid
from collections.abc import Mapping
from datetime import date
from typing import Protocol


class StockSource(Protocol):
    """Текущий остаток по артикулам. Его собирает зеркало wb_core, не мы."""

    async def stock(self, seller_id: uuid.UUID, today: date) -> dict[str, int] | None:
        """None — источник до селлера ещё не доходил; пусто — свежих данных нет."""
        ...


class ReviewCounts(Protocol):
    @property
    def total(self) -> int: ...

    @property
    def with_photo(self) -> int | None: ...

    @property
    def with_video(self) -> int | None: ...


class ReviewSource(Protocol):
    """Отзывы по артикулам на последний сбор. Их собирает зеркало wb_core."""

    async def totals(self, seller_id: uuid.UUID) -> Mapping[str, ReviewCounts] | None:
        """None — источник до селлера ещё не доходил; пусто — отзывов нет ни у одной карточки."""
        ...
