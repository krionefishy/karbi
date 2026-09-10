import uuid
from collections.abc import Mapping
from datetime import date
from typing import Protocol


class StockSource(Protocol):
    """Текущий остаток по артикулам. Его собирает оборачиваемость, не мы."""

    async def stock(self, seller_id: uuid.UUID, today: date) -> dict[str, int] | None:
        """None — селлер не подключён к источнику; пусто — свежих данных нет."""
        ...


class ReviewCounts(Protocol):
    @property
    def total(self) -> int: ...

    @property
    def with_photo(self) -> int | None: ...

    @property
    def with_video(self) -> int | None: ...


class ReviewSource(Protocol):
    """Отзывы по артикулам на последний срез. Их собирает автоматизация отзывов."""

    async def totals(self, seller_id: uuid.UUID) -> Mapping[str, ReviewCounts] | None:
        """None — селлер не подключён к отзывам; пусто — среза ещё не было."""
        ...
