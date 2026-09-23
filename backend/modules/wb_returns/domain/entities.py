"""Возвраты товаров продавцу и заявки покупателей на возврат — как их отдаёт WB.

Два разных потока с разными ключами: физический возврат живёт под стикером
(`shk_id`) и меняет статус по дороге в ПВЗ, заявка — под своим UUID и меняет
статус по решению продавца.
"""

from dataclasses import dataclass
from datetime import date, datetime

# Ключи статусов возврата. WB отдаёт статус словами и в разном регистре
# («В пути в пвз», «Готов к выдаче»), словарь сводит их к коду.
RETURN_READY = "ready"
RETURN_TRANSIT = "transit"
RETURN_ISSUED = "issued"
RETURN_NOT_PICKED = "not_picked"
RETURN_EXPIRED = "expired"
RETURN_CANCELLED = "cancelled"
RETURN_CREATED = "created"
RETURN_UNKNOWN = "unknown"

_STATUS_KEYS = {
    "готов к выдаче": RETURN_READY,
    "в пути в пвз": RETURN_TRANSIT,
    "выдано": RETURN_ISSUED,
    "неподбор": RETURN_NOT_PICKED,
    "истёк срок хранения": RETURN_EXPIRED,
    "истек срок хранения": RETURN_EXPIRED,
    "отмена оператором кц": RETURN_CANCELLED,
    "оформлен": RETURN_CREATED,
    "погружен в машину до сц/рц": RETURN_TRANSIT,
}

RETURN_STATUS_TITLES = {
    RETURN_READY: "Готов к выдаче",
    RETURN_TRANSIT: "В пути в ПВЗ",
    RETURN_ISSUED: "Выдано",
    RETURN_NOT_PICKED: "Неподбор",
    RETURN_EXPIRED: "Истёк срок хранения",
    RETURN_CANCELLED: "Отменён",
    RETURN_CREATED: "Оформлен",
    RETURN_UNKNOWN: "Статус не определён",
}

# По инструкции WB: в ПВЗ возврат хранится 7 дней, первые 3 — бесплатно,
# с 4-го по 7-й — 10 ₽ в день за штуку, на 8-й день товар уезжает на утилизацию.
FREE_STORAGE_DAYS = 3
STORAGE_DAYS = 7
STORAGE_FEE_PER_DAY = 10

# Заявку покупателя нужно рассмотреть за 5 дней, иначе она одобрится сама.
CLAIM_REVIEW_DAYS = 5
# `status` заявки в API: 0 — на рассмотрении, дальше — решение принято.
CLAIM_OPEN = 0


def status_key(status: str) -> str:
    return _STATUS_KEYS.get(status.strip().lower(), RETURN_UNKNOWN)


@dataclass(frozen=True, slots=True)
class ReturnItem:
    """Строка отчёта «Возвраты и перемещения» за одну единицу товара."""

    shk_id: int
    sticker_id: str
    srid: str
    order_id: int
    nm_id: int
    barcode: str
    brand: str
    subject_name: str
    tech_size: str
    return_type: str
    reason: str
    status: str
    is_active: bool
    dst_office_id: int | None
    dst_office_address: str
    order_dt: date | None
    ready_to_return_dt: datetime | None
    expired_dt: datetime | None
    completed_dt: datetime | None

    @property
    def status_key(self) -> str:
        return status_key(self.status)

    @property
    def title(self) -> str:
        """Как назвать товар в сообщении: предмет и бренд, размер — если не нулевой."""
        parts = [self.subject_name or f"артикул {self.nm_id}"]
        if self.brand:
            parts.append(self.brand)
        if self.tech_size and self.tech_size != "0":
            parts.append(f"размер {self.tech_size}")
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class Claim:
    """Заявка покупателя на возврат, как она лежит в `GET /api/v1/claims`."""

    id: str
    claim_type: int
    status: int
    status_ex: int
    nm_id: int
    imt_name: str
    user_comment: str
    wb_comment: str
    dt: datetime
    order_dt: datetime | None
    dt_update: datetime | None
    delivery_dt: datetime | None
    price: float
    currency_code: str
    srid: str
    photos: tuple[str, ...]
    video_paths: tuple[str, ...]
    actions: tuple[str, ...]
    is_archive: bool

    @property
    def is_open(self) -> bool:
        return self.status == CLAIM_OPEN and not self.is_archive

    @property
    def photo_urls(self) -> tuple[str, ...]:
        # WB отдаёт ссылки без схемы («//claim-basket-01.wbbasket.ru/…»).
        return tuple(f"https:{url}" if url.startswith("//") else url for url in self.photos)


@dataclass(frozen=True, slots=True)
class ReturnChange:
    """Возврат, у которого после сбора изменился статус (или он появился впервые)."""

    item: ReturnItem
    previous_status_key: str | None
