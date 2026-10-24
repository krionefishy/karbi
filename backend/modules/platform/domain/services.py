from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Service:
    """Сервис, доступ к которому выдаётся сотруднику. `id` стабильный, `title` можно менять."""

    id: str
    title: str


SERVICES: tuple[Service, ...] = (
    Service("wb-reviews", "Мониторинг отзывов"),
    Service("wb-turnover", "Оборачиваемость"),
    Service("wb-card-checklist", "Чек-лист карточек"),
    Service("wb-card-documents", "Документы карточек WB"),
    Service("wb-fbs-stocks", "Остатки FBS"),
    Service("wb-fbs-penalties", "Штрафы FBS"),
    Service("wb-review-chats", "Чаты после отзыва"),
    Service("wb-support-bot", "Бот поддержки"),
    Service("wb-returns", "Возвраты WB"),
    Service("wb-podsort", "Подсорт WB"),
    Service("fin-reports", "Финансовые отчёты"),
    Service("wb-box-stickers", "Стикеры коробов"),
)

SERVICE_IDS: frozenset[str] = frozenset(service.id for service in SERVICES)
