from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DocumentFacts:
    """The `documents` block of a card, reduced to what the checklist judges.

    `verified` documents passed WB's registry check, `rejected` ones came back
    with status 2 (the reasons are kept as WB sent them), the rest are still on
    review. `excluded` is the seller's own confirmation that the card needs none.
    """

    items: int
    verified: int
    rejected: int
    rejection_reasons: tuple[str, ...]
    excluded: bool


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
    documents: DocumentFacts | None = None


@dataclass(frozen=True, slots=True)
class SubjectCharacteristic:
    """One characteristic a category offers, as the WB directory lists it."""

    charc_id: int
    name: str
    required: bool
    popular: bool
    named_field: bool


@dataclass(frozen=True, slots=True)
class PriceFacts:
    article: str
    price: float
    discounted_price: float
    discount: int
    club_discount: int
    club_discounted_price: float | None = None


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    """Reviews of the card as the buyer sees them: summed over the склейка.

    `with_photo` / `with_video` are None until the reviews automation has
    counted media at least once. `latest_ratings` are the ratings of the five
    freshest reviews of the whole склейка, newest first: None — not recorded
    yet, an empty tuple — the card has no reviews.
    """

    total: int
    with_photo: int | None
    with_video: int | None
    latest_ratings: tuple[int, ...] | None = None
