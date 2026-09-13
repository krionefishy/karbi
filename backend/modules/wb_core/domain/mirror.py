from dataclasses import dataclass

# Виды зеркала: по каждому у селлера своя отметка сбора.
MIRROR_CATALOG = "catalog"
MIRROR_STOCKS = "stocks"
MIRROR_REVIEWS = "reviews"
MIRROR_KINDS = (MIRROR_CATALOG, MIRROR_STOCKS, MIRROR_REVIEWS)


@dataclass(frozen=True, slots=True)
class StockFact:
    """Текущий остаток карточки: FBO из отчёта аналитики, FBS суммой по складам продавца.

    `fbo_quantity_full` — с товаром в пути к клиенту и от клиента. Отчёт WB
    поле больше не отдаёт, число складывается у нас; оборачиваемость его не
    читает, но колонка держит прежний смысл.
    """

    article: str
    fbo_quantity: int
    fbo_quantity_full: int
    fbs_quantity: int

    @property
    def total(self) -> int:
        return self.fbo_quantity + self.fbs_quantity


@dataclass(frozen=True, slots=True)
class ReviewFact:
    """Отзывы карточки по звёздам. Медиа `None` — не считали, что не спутать с «ни одного»."""

    article: str
    ratings: tuple[int, int, int, int, int]
    with_photo: int | None
    with_video: int | None

    @property
    def total(self) -> int:
        return sum(self.ratings)
