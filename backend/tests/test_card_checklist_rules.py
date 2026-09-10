from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_card_checklist.domain import (
    COUNTED_ITEMS,
    ITEMS,
    ArticleFacts,
    CardFacts,
    CharacteristicsFill,
    ItemState,
    PriceFacts,
    ReviewFacts,
    SubjectCharacteristic,
    Thresholds,
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


def charc(charc_id: int, *, named: bool = False) -> SubjectCharacteristic:
    return SubjectCharacteristic(charc_id, f"Поле {charc_id}", False, False, named)


# Пять содержательных полей, плюс то, что считать нельзя: «Описание» живёт
# отдельным полем карточки, ИКПУ — учётный код.
DIRECTORY = (charc(1), charc(2), charc(3), charc(4), charc(5), charc(14177452, named=True), charc(15001650))


def facts(**overrides: Any) -> ArticleFacts:
    values: dict[str, Any] = {
        "card": card(),
        "characteristics": DIRECTORY,
        "price": PriceFacts("100", 179999, 8999.95, 95, 4),
        "reviews": ReviewFacts(total=52, with_photo=8, with_video=6),
    }
    values.update(overrides)
    return ArticleFacts(**values)


def states(article_facts: ArticleFacts) -> dict[str, ItemState]:
    return {state.key: state for state in evaluate(article_facts, THRESHOLDS)}


def test_only_what_wb_shows_is_in_the_table() -> None:
    assert [item.title for item in ITEMS] == [
        "Описание (SEO)",
        "Характеристики",
        "Фото",
        "Видео",
        "Скидка / СПП",
        "Отзывы есть",
        "Отзывы с фото",
        "Отзывы с видео",
    ]
    # Характеристики — справка без правила, в «готово» не входят.
    assert [item.key for item in ITEMS if not item.counted] == ["characteristics"]
    assert len(COUNTED_ITEMS) == 7


def test_a_complete_card_is_done_everywhere_it_can_be() -> None:
    result = states(facts())

    assert {key: state.done for key, state in result.items()} == {
        "description": True,
        "characteristics": None,
        "photos": True,
        "video": True,
        "discount": True,
        "reviews_present": True,
        "reviews_with_photo": True,
        "reviews_with_video": True,
    }
    assert result["reviews_with_photo"].detail == "8 с фото"


def test_photos_need_three_and_video_needs_to_exist() -> None:
    result = states(facts(card=card(photo_count=2, has_video=False)))

    assert (result["photos"].done, result["photos"].detail) == (False, "2 фото")
    assert (result["video"].done, result["video"].detail) == (False, "нет")
    assert states(facts(card=card(photo_count=3)))["photos"].done


def test_description_and_reviews_are_about_presence() -> None:
    result = states(facts(card=card(description_length=0), reviews=ReviewFacts(0, 0, 0)))

    assert (result["description"].done, result["description"].detail) == (False, "нет описания")
    assert (result["reviews_present"].done, result["reviews_present"].detail) == (False, "0 отз.")
    assert (result["reviews_with_video"].done, result["reviews_with_video"].detail) == (False, "0 с видео")
    assert states(facts(card=card(description_length=300)))["description"].done


def test_characteristics_show_what_is_filled_without_judging() -> None:
    state = states(facts(card=card(characteristic_ids=frozenset({1, 2, 3, 4}))))["characteristics"]

    # ИКПУ и «Описание» в справочнике есть, но в счёт не входят.
    assert characteristics_fill(card(), DIRECTORY) == CharacteristicsFill(filled=5, total=5, missing=())
    assert (state.done, state.detail, state.note) == (None, "4/5", "Не заполнены: Поле 5")


def test_a_long_list_of_empty_characteristics_is_cut_short() -> None:
    directory = tuple(charc(index) for index in range(1, 9))
    state = states(facts(card=card(characteristic_ids=frozenset()), characteristics=directory))["characteristics"]

    assert state.note == "Не заполнены: Поле 1, Поле 2, Поле 3, Поле 4, Поле 5 и ещё 3"


def test_missing_data_reads_as_unknown_not_as_failed() -> None:
    result = states(facts(characteristics=None, price=None, reviews=None))

    for key in ("discount", "reviews_present", "reviews_with_photo", "reviews_with_video"):
        assert result[key].done is None, key
    assert result["characteristics"].detail == "справочник не прочитан"
    # Отзывы до первого подсчёта медиа: всего знаем, с фото — ещё нет.
    counted = states(facts(reviews=ReviewFacts(total=4, with_photo=None, with_video=None)))
    assert counted["reviews_present"].done
    assert (counted["reviews_with_photo"].done, counted["reviews_with_photo"].detail) == (None, "ещё не считали")


def test_discount_shows_the_seller_discount_and_the_price_after_it() -> None:
    state = states(facts())["discount"]
    no_discount = states(facts(price=PriceFacts("100", 1000, 1000, 0, 0)))["discount"]

    assert (state.done, state.detail) == (True, "−95% · 9 000 ₽")
    assert (no_discount.done, no_discount.detail) == (False, "без скидки · 1 000 ₽")
