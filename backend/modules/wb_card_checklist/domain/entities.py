from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CardFacts:
    """What the card itself says, as the last collection read it from the content API."""

    article: str
    vendor_code: str
    title: str
    barcode: str
    imt_id: int | None
    subject_id: int | None
    subject_name: str
    photo_url: str
    photo_count: int
    description_length: int
    has_video: bool
    characteristic_ids: frozenset[int]
    card_created_at: datetime | None


@dataclass(frozen=True, slots=True)
class SubjectCharacteristic:
    """One characteristic a category offers, as the WB directory lists it."""

    charc_id: int
    name: str
    required: bool
    popular: bool
    # Заполняется отдельным полем карточки (бренд, описание, габариты), а не
    # в списке характеристик, поэтому в «заполнено из …» не считается.
    named_field: bool


@dataclass(frozen=True, slots=True)
class PriceFacts:
    article: str
    price: float
    discounted_price: float
    discount: int
    club_discount: int
    # Цена для подписчиков WB Клуба. None — WB её не прислал, тогда клубной
    # скидки нет и покупатель видит обычную цену со скидкой.
    club_discounted_price: float | None = None


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    """Reviews of the card as the buyer sees them: summed over the склейка.

    `with_photo` / `with_video` are None until the reviews automation has
    counted media at least once.
    """

    total: int
    with_photo: int | None
    with_video: int | None
