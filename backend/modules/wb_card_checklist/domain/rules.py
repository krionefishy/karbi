from collections.abc import Sequence
from dataclasses import dataclass

from backend.modules.wb_card_checklist.domain.entities import (
    CardFacts,
    PriceFacts,
    ReviewFacts,
    SubjectCharacteristic,
)
from backend.modules.wb_card_checklist.domain.items import ITEMS

# Характеристики, которые есть в справочнике почти любой категории, но к
# содержанию карточки отношения не имеют: учётные коды, налог, документы.
SERVICE_CHARACTERISTICS = frozenset(
    {
        14177453,  # SKU
        15000001,  # ТНВЭД — в части категорий под этим id, а не под 15004139
        15001135,  # Номер декларации соответствия
        15001136,  # Номер сертификата соответствия
        15001137,  # Дата регистрации сертификата/декларации
        15001138,  # Дата окончания действия сертификата/декларации
        15001405,  # Ставка НДС
        15001650,  # ИКПУ
        15001706,  # Код упаковки
        15003293,  # Артикул OZON
        15003988,  # NTIN
        15004139,  # Код ТН ВЭД
        15004301,  # КТД для Республики Беларусь
    }
)
# Сколько незаполненных характеристик называть в подсказке поимённо.
NAMED_MISSING = 5


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Единственный порог среди пунктов — фото. Второй порог, остаток, отбирает строки, а не пункты."""

    min_photos: int = 3


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
    # Выполнено ли по данным WB. None — данных нет или пункт справочный:
    # «не знаем» не должно читаться как «не выполнено».
    done: bool | None
    # Что видит WB: «22/25», «8 с фото».
    detail: str | None = None
    # Пояснение к детали: каких характеристик не хватает.
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
        "discount": _discount(facts.price),
        **_reviews(facts.reviews),
    }
    return [computed[item.key] for item in ITEMS]


def _characteristics(facts: ArticleFacts) -> ItemState:
    """Справка без правила: сколько заполнено и чего не хватает."""
    if not facts.characteristics:
        return ItemState("characteristics", None, "справочник не прочитан")
    fill = characteristics_fill(facts.card, facts.characteristics)
    return ItemState("characteristics", None, f"{fill.filled}/{fill.total}", _missing_note(fill.missing))


def _missing_note(missing: tuple[str, ...]) -> str | None:
    if not missing:
        return None
    named = ", ".join(missing[:NAMED_MISSING])
    rest = len(missing) - NAMED_MISSING
    return f"Не заполнены: {named}" + (f" и ещё {rest}" if rest > 0 else "")


def _discount(price: PriceFacts | None) -> ItemState:
    if price is None:
        return ItemState("discount", None, "нет цены")
    shown = f"{price.discounted_price:,.0f}".replace(",", " ")
    label = f"−{price.discount}%" if price.discount else "без скидки"
    return ItemState("discount", price.discount > 0, f"{label} · {shown} ₽")


def _reviews(reviews: ReviewFacts | None) -> dict[str, ItemState]:
    if reviews is None:
        return {key: ItemState(key, None, "нет среза") for key in REVIEW_KEYS}
    return {
        "reviews_present": ItemState("reviews_present", reviews.total > 0, f"{reviews.total} отз."),
        "reviews_with_photo": _media("reviews_with_photo", reviews.with_photo, "с фото"),
        "reviews_with_video": _media("reviews_with_video", reviews.with_video, "с видео"),
    }


REVIEW_KEYS = ("reviews_present", "reviews_with_photo", "reviews_with_video")


def _media(key: str, count: int | None, label: str) -> ItemState:
    if count is None:
        return ItemState(key, None, "ещё не считали")
    return ItemState(key, count > 0, f"{count} {label}")
