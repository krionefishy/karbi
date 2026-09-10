from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

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
    kind: ItemKind
    checked: bool
    # Может ли менеджер поставить отметку. Снять свою он может всегда.
    can_check: bool
    # Что видит API: «25/38», «8 с фото». Пусто — сказать нечего.
    detail: str | None = None
    # Пояснение к детали: каких характеристик не хватает.
    note: str | None = None
    # Отметка стоит, а факт, на котором она держалась, пропал.
    warning: str | None = None
    # Данных для решения нет: «не знаем», а не «не выполнено».
    unknown: bool = False


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


def evaluate(facts: ArticleFacts, marks: Mapping[str, bool], thresholds: Thresholds) -> list[ItemState]:
    """All items of one card, in the order of the table."""
    card = facts.card
    computed = {
        "description": _confirm(
            "description",
            card.description_length > 0,
            marks,
            f"{card.description_length} симв." if card.description_length else "нет описания",
        ),
        "characteristics": _characteristics(facts, marks),
        "photos": _auto("photos", card.photo_count >= thresholds.min_photos, f"{card.photo_count} фото"),
        "video": _auto("video", card.has_video, "есть" if card.has_video else "нет"),
        "discount": _discount(facts.price, marks),
        **_reviews(facts.reviews, marks),
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


def _characteristics(facts: ArticleFacts, marks: Mapping[str, bool]) -> ItemState:
    """WB подсказывает, сколько заполнено и чего не хватает; готовность решает менеджер."""
    if not facts.characteristics:
        return _confirm("characteristics", None, marks, "справочник не прочитан")
    fill = characteristics_fill(facts.card, facts.characteristics)
    state = _confirm("characteristics", fill.filled > 0, marks, f"{fill.filled}/{fill.total}")
    return replace(state, note=_missing_note(fill.missing))


def _missing_note(missing: tuple[str, ...]) -> str | None:
    if not missing:
        return None
    named = ", ".join(missing[:NAMED_MISSING])
    rest = len(missing) - NAMED_MISSING
    return f"Не заполнены: {named}" + (f" и ещё {rest}" if rest > 0 else "")


def _discount(price: PriceFacts | None, marks: Mapping[str, bool]) -> ItemState:
    if price is None:
        return _confirm("discount", None, marks, "нет цены")
    shown = f"{price.discounted_price:,.0f}".replace(",", " ")
    return _confirm("discount", price.discount > 0, marks, f"−{price.discount}% · {shown} ₽")


def _reviews(reviews: ReviewFacts | None, marks: Mapping[str, bool]) -> dict[str, ItemState]:
    if reviews is None:
        return {
            "reviews_present": _auto("reviews_present", None, "нет среза"),
            "reviews_with_photo": _confirm("reviews_with_photo", None, marks, "нет среза"),
            "reviews_with_video": _confirm("reviews_with_video", None, marks, "нет среза"),
        }
    return {
        "reviews_present": _auto("reviews_present", reviews.total > 0, f"{reviews.total} отз."),
        "reviews_with_photo": _media("reviews_with_photo", reviews.with_photo, "с фото", marks),
        "reviews_with_video": _media("reviews_with_video", reviews.with_video, "с видео", marks),
    }


def _media(key: str, count: int | None, label: str, marks: Mapping[str, bool]) -> ItemState:
    if count is None:
        return _confirm(key, None, marks, "ещё не считали")
    return _confirm(key, count > 0, marks, f"{count} {label}")
