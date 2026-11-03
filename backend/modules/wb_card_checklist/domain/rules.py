from collections.abc import Sequence
from dataclasses import dataclass

from backend.modules.wb_card_checklist.domain.entities import (
    CardFacts,
    PriceFacts,
    ReviewFacts,
    SubjectCharacteristic,
)
from backend.modules.wb_card_checklist.domain.items import ITEMS

SERVICE_CHARACTERISTICS = frozenset(
    {
        14177453,
        15000001,
        15001135,
        15001136,
        15001137,
        15001138,
        15001405,
        15001650,
        15001706,
        15003293,
        15003988,
        15004139,
        15004301,
    }
)
NAMED_MISSING = 5
DECLARATION_CHARACTERISTICS = frozenset({15001135, 15001136})
DOCUMENT_REASONS = {
    "document_missing": "документ не найден",
    "document_not_found": "нет в реестре",
    "document_inactive": "недействителен",
    "document_expired": "истёк",
    "document_dates_mismatch": "даты не сходятся",
    "document_type_mismatch": "не тот тип",
    "applicant_mismatch": "заявитель не совпадает",
    "trade_name_mismatch": "название не совпадает",
}


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Единственный порог среди пунктов — фото. Второй порог, остаток, отбирает строки, а не пункты."""

    min_photos: int = 1


@dataclass(frozen=True, slots=True)
class ArticleFacts:
    """Everything known about one card at the moment the checklist is read.

    None in a field means «нет данных» — the collection has not run, or the
    automation owning the numbers is not connected — never «нет».
    """

    card: CardFacts
    characteristics: Sequence[SubjectCharacteristic] | None
    price: PriceFacts | None
    reviews: ReviewFacts | None


@dataclass(frozen=True, slots=True)
class ItemState:
    key: str
    done: bool | None
    detail: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CharacteristicsFill:
    filled: int
    total: int
    missing: tuple[str, ...]


def characteristics_fill(card: CardFacts, characteristics: Sequence[SubjectCharacteristic]) -> CharacteristicsFill:
    relevant = [
        item for item in characteristics if not item.named_field and item.charc_id not in SERVICE_CHARACTERISTICS
    ]
    missing = tuple(item.name for item in relevant if item.charc_id not in card.characteristic_ids)
    return CharacteristicsFill(len(relevant) - len(missing), len(relevant), missing)


def evaluate(facts: ArticleFacts, thresholds: Thresholds) -> list[ItemState]:
    """All items of one card, in the order of the table."""
    card = facts.card
    computed = {
        "description": ItemState(
            "description",
            card.description_length > 0,
            f"{card.description_length} симв." if card.description_length else "нет описания",
        ),
        "characteristics": _characteristics(facts),
        "photos": ItemState("photos", card.photo_count >= thresholds.min_photos, f"{card.photo_count} фото"),
        "video": ItemState("video", card.has_video, "есть" if card.has_video else "нет"),
        "documents": _documents(card),
        "discount": _discount(facts.price),
        "club_discount": _club_discount(facts.price),
        **_reviews(facts.reviews),
    }
    return [computed[item.key] for item in ITEMS]


def _documents(card: CardFacts) -> ItemState:
    """Документы карточки: приняты WB, не приняты, не загружены или не нужны.

    Нет документов и нет номера декларации в характеристиках — категории они не нужны
    (фонари, фитолампы, отпариватели): это не проблема. Номер есть, документов нет — не загружены.
    """
    documents = card.documents
    if documents is None:
        return ItemState("documents", None, "ещё не считали")
    if documents.excluded:
        return ItemState("documents", True, "не требуются")
    if documents.rejected:
        reasons = ", ".join(
            dict.fromkeys(DOCUMENT_REASONS.get(reason, reason) for reason in documents.rejection_reasons)
        )
        return ItemState("documents", False, f"отклонены: {reasons}" if reasons else "отклонены")
    if not documents.items:
        if card.characteristic_ids & DECLARATION_CHARACTERISTICS:
            return ItemState("documents", False, "не загружены")
        return ItemState("documents", True, "не требуются")
    if documents.verified < documents.items:
        return ItemState("documents", None, "на проверке")
    return ItemState("documents", True, f"приняты ({documents.items})")


def _characteristics(facts: ArticleFacts) -> ItemState:
    """Сколько заполнено и чего не хватает — для сведения, а не как ошибка.

    Заполнять все характеристики категории селлер не требует: пункт зелёный,
    как только справочник прочитан, а пустые поля видны в подсказке ячейки.
    """
    if not facts.characteristics:
        return ItemState("characteristics", None, "справочник не прочитан")
    fill = characteristics_fill(facts.card, facts.characteristics)
    return ItemState("characteristics", True, f"{fill.filled}/{fill.total}", _missing_note(fill.missing))


def _missing_note(missing: tuple[str, ...]) -> str | None:
    if not missing:
        return None
    named = ", ".join(missing[:NAMED_MISSING])
    rest = len(missing) - NAMED_MISSING
    return f"Не заполнены: {named}" + (f" и ещё {rest}" if rest > 0 else "")


def _discount(price: PriceFacts | None) -> ItemState:
    if price is None:
        return ItemState("discount", None, "нет цены")
    label = f"−{price.discount}%" if price.discount else "без скидки"
    return ItemState("discount", price.discount > 0, f"{label} · {_rubles(price.discounted_price)}")


def _club_discount(price: PriceFacts | None) -> ItemState:
    if price is None:
        return ItemState("club_discount", None, "нет цены")
    if not price.club_discount:
        return ItemState("club_discount", False, "без скидки")
    club_price = price.club_discounted_price if price.club_discounted_price is not None else price.discounted_price
    return ItemState("club_discount", True, f"−{price.club_discount}% · {_rubles(club_price)}")


def _rubles(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def _reviews(reviews: ReviewFacts | None) -> dict[str, ItemState]:
    if reviews is None:
        return {key: ItemState(key, None, "нет среза") for key in REVIEW_KEYS}
    return {
        "reviews_present": ItemState("reviews_present", reviews.total > 0, f"{reviews.total} отз."),
        "reviews_with_photo": _media("reviews_with_photo", reviews.with_photo, "с фото"),
        "reviews_with_video": _media("reviews_with_video", reviews.with_video, "с видео"),
        "negative_recent": _negative_recent(reviews.latest_ratings),
    }


def _negative_recent(latest: tuple[int, ...] | None) -> ItemState:
    """Оценки 1–3 среди пяти самых свежих отзывов склейки. Отзывов нет — «нет данных», а не «чисто»."""
    if latest is None:
        return ItemState("negative_recent", None, "ещё не считали")
    if not latest:
        return ItemState("negative_recent", None, "отзывов нет")
    negative = sum(1 for rating in latest if rating <= NEGATIVE_MAX_RATING)
    if negative == 0:
        return ItemState("negative_recent", True, f"нет (из {len(latest)})")
    return ItemState("negative_recent", False, f"есть: {negative} из {len(latest)}")


REVIEW_KEYS = ("reviews_present", "reviews_with_photo", "reviews_with_video", "negative_recent")
NEGATIVE_MAX_RATING = 3


def _media(key: str, count: int | None, label: str) -> ItemState:
    if count is None:
        return ItemState(key, None, "ещё не считали")
    return ItemState(key, count > 0, f"{count} {label}")
