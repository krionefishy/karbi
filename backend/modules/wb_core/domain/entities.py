import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

ARTICLE_STATES = ("active", "archived", "feedback_only")

# Маркетплейсы, по которым у селлера бывает учётка. Строки те же, что в
# путях шлюза и в ответе его /api/v1/sellers.
MARKETPLACE_WB = "wb"
MARKETPLACE_OZON = "ozon"
# Не маркетплейс, а аналитика по ним: токен MPStats у селлера свой, и учётка
# устроена так же — поэтому стоит в том же ряду и под тем же словом.
MARKETPLACE_MPSTATS = "mpstats"
# Все учётки, исход доставки которых реестр умеет хранить. Имя, которого здесь
# нет, шлюз мог выучить раньше нас — такое пропускается, а не роняет сверку.
MARKETPLACES = (MARKETPLACE_WB, MARKETPLACE_OZON, MARKETPLACE_MPSTATS)

# Сага доставки учётки на шлюз wb-egress: первую группу статусов отвечает сам
# шлюз, вторая описывает доставку с нашей стороны. Единственный словарь этих
# строк — здесь; фронтенд повторяет его в features/sellers/types.ts.
# Словарь общий для маркетплейсов: у Ozon те же исходы, что у Wildberries.
EGRESS_VERIFIED = "verified"
EGRESS_DELIVERED = "delivered"
EGRESS_KEY_INVALID = "key_invalid"
EGRESS_NO_FREE_IP = "no_free_ip"
EGRESS_DISABLED = "disabled"
EGRESS_UNDELIVERED = "undelivered"  # шлюз недоступен, ключ не доехал
EGRESS_UNSYNCED = "unsynced"  # локальная правка есть, шлюз о ней не знает
# Статусы, при которых по селлеру можно работать с WB.
EGRESS_SERVABLE = frozenset({EGRESS_VERIFIED, EGRESS_DELIVERED})


@dataclass(frozen=True, slots=True)
class Seller:
    id: uuid.UUID
    name: str
    product_count: int
    catalog_sync_status: str
    last_catalog_sync_at: datetime | None
    catalog_sync_error: str | None
    archived_at: datetime | None = None
    egress_status: str = EGRESS_UNDELIVERED
    egress_error: str | None = None
    ozon_egress_status: str = EGRESS_UNDELIVERED
    ozon_egress_error: str | None = None
    mpstats_egress_status: str = EGRESS_UNDELIVERED
    mpstats_egress_error: str | None = None
    egress_ip: str | None = None

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


@dataclass(frozen=True, slots=True)
class TaxRate:
    """Ставка налога селлера в процентах, действующая с `effective_from`.

    Ставка — свойство юрлица, а не маркетплейса: у селлера она одна на WB и
    Ozon. Версии не переписываются: ставки меняются с нового года, и расчёт
    прошлого периода должен остаться со своей.
    """

    effective_from: date
    rate: Decimal


def tax_rate_on(rates: Sequence[TaxRate], day: date) -> TaxRate | None:
    """Ставка, действовавшая в `day`; до самой первой версии — она же, как у себестоимости."""
    ordered = sorted(rates, key=lambda item: item.effective_from)
    if not ordered:
        return None
    current = ordered[0]
    for rate in ordered:
        if rate.effective_from > day:
            break
        current = rate
    return current


@dataclass(frozen=True, slots=True)
class Article:
    id: uuid.UUID
    seller_id: uuid.UUID
    article: str
    vendor_code: str
    name: str
    imt_id: int | None = None
    brand: str = ""
    subject_id: int | None = None
    subject_name: str = ""
    photo_url: str = ""
    photo_count: int | None = None
    state: str = "active"


@dataclass(frozen=True, slots=True)
class BarcodeCard:
    """Баркод из каталога: чья это карточка и какой размер."""

    barcode: str
    article: str
    vendor_code: str
    subject_name: str
    tech_size: str
