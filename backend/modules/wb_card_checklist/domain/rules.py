import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.modules.wb_card_checklist.domain.entities import (
    CardFacts,
    PriceFacts,
    ReviewFacts,
    SubjectCharacteristic,
)
from backend.modules.wb_card_checklist.domain.items import ITEMS, ItemKind

# Характеристики, которые есть в справочнике почти любой категории, но к
# полноте карточки отношения не имеют: учётные коды, налог, документы.
# Считать их — значит требовать ИКПУ ради зелёной галочки.
SERVICE_CHARACTERISTICS = frozenset(
    {
        14177453,  # SKU
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
    }
)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Где проходит граница «выполнено». Все числа — допущения до ответа селлера."""

    min_photos: int = 3
    min_description_length: int = 1000
    min_reviews: int = 1
    # Доля заполненных характеристик сверх обязательных и популярных: все
    # до одной — недостижимая планка, в справочнике бывают поля «на всякий случай».
    characteristics_share: float = 0.8


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
    kind: ItemKind
    checked: bool
    # Может ли менеджер поставить отметку. Снять свою он может всегда.
    can_check: bool
    # Что видит API: «25/38», «8 с фото». Пусто — сказать нечего.
    detail: str | None = None
    # Пояснение к детали: каких ключевых характеристик не хватает.
    note: str | None = None
    # Отметка стоит, а факт, на котором она держалась, пропал.
    warning: str | None = None
    # Данных для решения нет: «не знаем», а не «не выполнено».
    unknown: bool = False


@dataclass(frozen=True, slots=True)
class CharacteristicsFill:
    filled: int
    total: int
    key_missing: tuple[str, ...]

    def complete(self, share: float) -> bool:
        # round: 0.8 * 15 в двоичной арифметике — 12.000000000000002, и ceil
        # без округления потребовал бы тринадцать.
        return not self.key_missing and self.filled >= math.ceil(round(share * self.total, 9))


def characteristics_fill(card: CardFacts, characteristics: Sequence[SubjectCharacteristic]) -> CharacteristicsFill:
    relevant = [
        item for item in characteristics if not item.named_field and item.charc_id not in SERVICE_CHARACTERISTICS
    ]
    filled = [item for item in relevant if item.charc_id in card.characteristic_ids]
    key_missing = tuple(
        item.name
        for item in relevant
        if (item.required or item.popular) and item.charc_id not in card.characteristic_ids
    )
    return CharacteristicsFill(len(filled), len(relevant), key_missing)


def evaluate(facts: ArticleFacts, marks: Mapping[str, bool], thresholds: Thresholds) -> list[ItemState]:
    """All thirteen items of one card, in the order of the table."""
    card = facts.card
    computed = {
        "description": _confirm(
            "description",
            card.description_length >= thresholds.min_description_length,
            marks,
            f"{card.description_length} симв." if card.description_length else "нет описания",
        ),
        "characteristics": _characteristics(facts, thresholds),
        "photos": _auto("photos", card.photo_count >= thresholds.min_photos, f"{card.photo_count} фото"),
        "video": _auto("video", card.has_video, "есть" if card.has_video else "нет"),
        "discount": _discount(facts.price, marks),
        **_reviews(facts.reviews, marks, thresholds),
    }
    return [computed.get(item.key) or _manual(item.key, marks) for item in ITEMS]


def accepts(state: ItemState, checked: bool) -> bool:
    """Whether a manager may set this item to `checked` by hand."""
    if state.kind is ItemKind.AUTO:
        return False
    if state.kind is ItemKind.MANUAL or not checked:
        return True
    return state.can_check


def _auto(key: str, value: bool | None, detail: str | None, note: str | None = None) -> ItemState:
    return ItemState(
        key, ItemKind.AUTO, checked=bool(value), can_check=False, detail=detail, note=note, unknown=value is None
    )


def _manual(key: str, marks: Mapping[str, bool]) -> ItemState:
    return ItemState(key, ItemKind.MANUAL, checked=marks.get(key, False), can_check=True)


def _confirm(key: str, fact: bool | None, marks: Mapping[str, bool], detail: str | None) -> ItemState:
    checked = marks.get(key, False)
    if fact is None:
        return ItemState(key, ItemKind.CONFIRM, checked=checked, can_check=False, detail=detail, unknown=True)
    warning = "Отметка стоит, но в данных WB этого больше нет" if checked and not fact else None
    return ItemState(key, ItemKind.CONFIRM, checked=checked, can_check=fact, detail=detail, warning=warning)


def _characteristics(facts: ArticleFacts, thresholds: Thresholds) -> ItemState:
    if not facts.characteristics:
        return _auto("characteristics", None, "справочник не прочитан")
    fill = characteristics_fill(facts.card, facts.characteristics)
    note = f"Не заполнены: {', '.join(fill.key_missing)}" if fill.key_missing else None
    return _auto(
        "characteristics", fill.complete(thresholds.characteristics_share), f"{fill.filled}/{fill.total}", note
    )


def _discount(price: PriceFacts | None, marks: Mapping[str, bool]) -> ItemState:
    if price is None:
        return _confirm("discount", None, marks, "нет цены")
    shown = f"{price.discounted_price:,.0f}".replace(",", " ")
    return _confirm("discount", price.discount > 0, marks, f"−{price.discount}% · {shown} ₽")


def _reviews(reviews: ReviewFacts | None, marks: Mapping[str, bool], thresholds: Thresholds) -> dict[str, ItemState]:
    if reviews is None:
        return {
            "reviews_present": _auto("reviews_present", None, "нет среза"),
            "reviews_with_photo": _confirm("reviews_with_photo", None, marks, "нет среза"),
            "reviews_with_video": _confirm("reviews_with_video", None, marks, "нет среза"),
        }
    return {
        "reviews_present": _auto("reviews_present", reviews.total >= thresholds.min_reviews, f"{reviews.total} отз."),
        "reviews_with_photo": _media("reviews_with_photo", reviews.with_photo, "с фото", marks),
        "reviews_with_video": _media("reviews_with_video", reviews.with_video, "с видео", marks),
    }


def _media(key: str, count: int | None, label: str, marks: Mapping[str, bool]) -> ItemState:
    if count is None:
        return _confirm(key, None, marks, "ещё не считали")
    return _confirm(key, count > 0, marks, f"{count} {label}")
