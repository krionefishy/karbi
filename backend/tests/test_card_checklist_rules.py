from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_card_checklist.domain import (
    ITEMS,
    ArticleFacts,
    CardFacts,
    CharacteristicsFill,
    ItemKind,
    ItemState,
    PriceFacts,
    ReviewFacts,
    SubjectCharacteristic,
    Thresholds,
    accepts,
    characteristics_fill,
    evaluate,
)

THRESHOLDS = Thresholds()


def card(**overrides: Any) -> CardFacts:
    values: dict[str, Any] = {
        "article": "100",
        "vendor_code": "SKU-100",
        "title": "Пила аккумуляторная",
        "barcode": "2054000592278",
        "imt_id": 1,
        "subject_id": 2224,
        "subject_name": "Электропилы цепные",
        "photo_url": "",
        "photo_count": 5,
        "description_length": 1500,
        "has_video": True,
        "characteristic_ids": frozenset({1, 2, 3, 4, 5}),
        "card_created_at": datetime(2026, 8, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return CardFacts(**values)


def charc(
    charc_id: int, *, required: bool = False, popular: bool = False, named: bool = False
) -> SubjectCharacteristic:
    return SubjectCharacteristic(charc_id, f"Поле {charc_id}", required, popular, named)


# Пять содержательных полей, плюс то, что считать нельзя: «Описание» живёт
# отдельным полем карточки, ИКПУ — учётный код.
DIRECTORY = (
    charc(1, popular=True),
    charc(2),
    charc(3),
    charc(4),
    charc(5),
    charc(14177452, named=True),
    charc(15001650),
)


def facts(**overrides: Any) -> ArticleFacts:
    values: dict[str, Any] = {
        "card": card(),
        "characteristics": DIRECTORY,
        "price": PriceFacts("100", 179999, 8999.95, 95, 4),
        "reviews": ReviewFacts(total=52, with_photo=8, with_video=6),
    }
    values.update(overrides)
    return ArticleFacts(**values)


def states(article_facts: ArticleFacts, marks: dict[str, bool] | None = None) -> dict[str, ItemState]:
    return {state.key: state for state in evaluate(article_facts, marks or {}, THRESHOLDS)}


def test_items_follow_the_spreadsheet_managers_kept_by_hand() -> None:
    # Кэшбек из ручной таблицы решили не отслеживать.
    assert [item.title for item in ITEMS] == [
        "Описание (SEO)",
        "Характеристики",
        "Фото",
        "Видео",
        "Видеообложка",
        "Рич-контент",
        "Скидка / СПП",
        "Отзывы за баллы",
        "Отзывы есть",
        "Отзывы с фото",
        "Отзывы с видео",
        "Оценки прицеплены",
    ]
    manual = {item.key for item in ITEMS if item.kind is ItemKind.MANUAL}
    assert manual == {"video_cover", "rich_content", "reviews_for_points", "ratings_linked"}


def test_characteristics_count_only_what_the_category_really_asks_for() -> None:
    fill = characteristics_fill(card(), DIRECTORY)

    # ИКПУ и «Описание» в справочнике есть, но в полноту не входят.
    assert fill == CharacteristicsFill(filled=5, total=5, missing=())
    assert states(facts())["characteristics"].detail == "5/5"


def test_characteristics_are_a_hint_and_the_manager_decides() -> None:
    """Правила «заполнено всё» нет: WB показывает пустые поля, галочку ставит менеджер."""
    state = states(facts(card=card(characteristic_ids=frozenset({1, 2, 3, 4}))))["characteristics"]

    assert state.kind is ItemKind.CONFIRM
    assert (state.checked, state.can_check) == (False, True)
    assert state.detail == "4/5"
    assert state.note == "Не заполнены: Поле 5"
    assert states(facts(), {"characteristics": True})["characteristics"].checked


def test_a_long_list_of_empty_characteristics_is_cut_short() -> None:
    directory = tuple(charc(index) for index in range(1, 9))
    state = states(facts(card=card(characteristic_ids=frozenset()), characteristics=directory))["characteristics"]

    assert state.note == "Не заполнены: Поле 1, Поле 2, Поле 3, Поле 4, Поле 5 и ещё 3"
    # Ни одного заполненного поля — отмечать нечего.
    assert not state.can_check


def test_automatic_items_follow_the_card() -> None:
    result = states(facts(card=card(photo_count=2, has_video=False), reviews=ReviewFacts(0, 0, 0)))

    assert (result["photos"].checked, result["photos"].detail) == (False, "2 фото")
    assert (result["video"].checked, result["video"].detail) == (False, "нет")
    assert (result["reviews_present"].checked, result["reviews_present"].detail) == (False, "0 отз.")
    assert states(facts(card=card(photo_count=3)))["photos"].checked
    assert not accepts(result["photos"], True)
    assert not accepts(result["photos"], False)


def test_a_confirmed_item_needs_the_fact_before_a_tick() -> None:
    empty = states(facts(card=card(description_length=0)))["description"]
    short = states(facts(card=card(description_length=300)))["description"]

    assert (empty.checked, empty.can_check, empty.detail) == (False, False, "нет описания")
    assert not accepts(empty, True)
    # Снять свою отметку можно всегда — даже когда ставить её уже не на чем.
    assert accepts(empty, False)
    # Порога длины нет: любое описание — повод менеджеру его проверить.
    assert (short.can_check, short.detail) == (True, "300 симв.")


def test_a_tick_stays_when_the_fact_is_gone_but_says_so() -> None:
    reviews = ReviewFacts(total=10, with_photo=0, with_video=0)
    state = states(facts(reviews=reviews), {"reviews_with_photo": True})["reviews_with_photo"]

    assert state.checked
    assert state.warning is not None
    assert state.detail == "0 с фото"


def test_missing_data_reads_as_unknown_not_as_failed() -> None:
    result = states(facts(characteristics=None, price=None, reviews=None))

    for key in ("characteristics", "discount", "reviews_present", "reviews_with_photo", "reviews_with_video"):
        assert result[key].unknown, key
        assert not result[key].checked, key
    # Отзывы до первого подсчёта медиа: всего знаем, с фото — ещё нет.
    counted = states(facts(reviews=ReviewFacts(total=4, with_photo=None, with_video=None)))
    assert counted["reviews_present"].checked
    assert counted["reviews_with_photo"].unknown


def test_manual_items_carry_the_managers_mark() -> None:
    result = states(facts(), {"video_cover": True})

    assert result["video_cover"].checked
    assert not result["rich_content"].checked
    assert accepts(result["rich_content"], True)


def test_discount_shows_the_seller_discount_and_the_price_after_it() -> None:
    state = states(facts())["discount"]

    assert state.detail == "−95% · 9 000 ₽"
    assert state.can_check
    assert not states(facts(price=PriceFacts("100", 1000, 1000, 0, 0)))["discount"].can_check
