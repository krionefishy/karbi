from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.modules.wb_card_checklist.application import ChecklistService
from backend.modules.wb_card_checklist.application.view import ChecklistRow
from backend.modules.wb_card_checklist.domain import (
    ITEMS,
    ArticleFacts,
    CardFacts,
    CharacteristicsFill,
    DocumentFacts,
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
        "documents": DocumentFacts(items=1, verified=1, rejected=0, rejection_reasons=(), excluded=False),
    }
    values.update(overrides)
    return CardFacts(**values)


def charc(charc_id: int, *, named: bool = False) -> SubjectCharacteristic:
    return SubjectCharacteristic(charc_id, f"Поле {charc_id}", False, False, named)


DIRECTORY = (charc(1), charc(2), charc(3), charc(4), charc(5), charc(14177452, named=True), charc(15001650))


def facts(**overrides: Any) -> ArticleFacts:
    values: dict[str, Any] = {
        "card": card(),
        "characteristics": DIRECTORY,
        "price": PriceFacts("100", 179999, 8999.95, 95, 4, 8639.95),
        "reviews": ReviewFacts(total=52, with_photo=8, with_video=6, latest_ratings=(5, 4, 5, 5, 4)),
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
        "Документы",
        "Скидка / СПП",
        "Скидка WB Клуба",
        "Отзывы есть",
        "Отзывы с фото",
        "Отзывы с видео",
        "Негатив в 5 свежих",
    ]


def test_a_complete_card_is_done_everywhere_it_can_be() -> None:
    result = states(facts())

    assert {key: state.done for key, state in result.items()} == {
        "description": True,
        "characteristics": True,
        "photos": True,
        "video": True,
        "documents": True,
        "discount": True,
        "club_discount": True,
        "reviews_present": True,
        "reviews_with_photo": True,
        "reviews_with_video": True,
        "negative_recent": True,
    }
    assert result["reviews_with_photo"].detail == "8 с фото"


def test_one_photo_and_one_video_are_enough() -> None:
    """По инструкции: «фото-контент установлен», «видео установлено»."""
    result = states(facts(card=card(photo_count=0, has_video=False)))

    assert (result["photos"].done, result["photos"].detail) == (False, "0 фото")
    assert (result["video"].done, result["video"].detail) == (False, "нет")
    assert states(facts(card=card(photo_count=1)))["photos"].done


def test_description_and_reviews_are_about_presence() -> None:
    result = states(facts(card=card(description_length=0), reviews=ReviewFacts(0, 0, 0)))

    assert (result["description"].done, result["description"].detail) == (False, "нет описания")
    assert (result["reviews_present"].done, result["reviews_present"].detail) == (False, "0 отз.")
    assert (result["reviews_with_video"].done, result["reviews_with_video"].detail) == (False, "0 с видео")
    assert states(facts(card=card(description_length=300)))["description"].done


def test_empty_characteristics_are_shown_but_not_an_error() -> None:
    """Заполнять все характеристики не требуется: пункт зелёный, пустые поля — в подсказке."""
    state = states(facts(card=card(characteristic_ids=frozenset({1, 2, 3, 4}))))["characteristics"]

    assert characteristics_fill(card(), DIRECTORY) == CharacteristicsFill(filled=5, total=5, missing=())
    assert (states(facts())["characteristics"].done, states(facts())["characteristics"].detail) == (True, "5/5")
    assert (state.done, state.detail, state.note) == (True, "4/5", "Не заполнены: Поле 5")


def test_a_long_list_of_empty_characteristics_is_cut_short() -> None:
    directory = tuple(charc(index) for index in range(1, 9))
    state = states(facts(card=card(characteristic_ids=frozenset()), characteristics=directory))["characteristics"]

    assert state.note == "Не заполнены: Поле 1, Поле 2, Поле 3, Поле 4, Поле 5 и ещё 3"


def test_missing_data_reads_as_unknown_not_as_failed() -> None:
    result = states(facts(characteristics=None, price=None, reviews=None))

    for key in (
        "characteristics",
        "discount",
        "club_discount",
        "reviews_present",
        "reviews_with_photo",
        "reviews_with_video",
    ):
        assert result[key].done is None, key
    assert result["characteristics"].detail == "справочник не прочитан"
    counted = states(facts(reviews=ReviewFacts(total=4, with_photo=None, with_video=None)))
    assert counted["reviews_present"].done
    assert (counted["reviews_with_photo"].done, counted["reviews_with_photo"].detail) == (None, "ещё не считали")


def test_discount_shows_the_seller_discount_and_the_price_after_it() -> None:
    state = states(facts())["discount"]
    no_discount = states(facts(price=PriceFacts("100", 1000, 1000, 0, 0)))["discount"]

    assert (state.done, state.detail) == (True, "−95% · 9 000 ₽")
    assert (no_discount.done, no_discount.detail) == (False, "без скидки · 1 000 ₽")


def test_club_discount_shows_the_club_price() -> None:
    club = states(facts())["club_discount"]
    no_club = states(facts(price=PriceFacts("100", 1000, 900, 10, 0)))["club_discount"]
    without_price = states(facts(price=PriceFacts("100", 1000, 900, 10, 3)))["club_discount"]

    assert (club.done, club.detail) == (True, "−4% · 8 640 ₽")
    assert (no_club.done, no_club.detail) == (False, "без скидки")
    assert without_price.detail == "−3% · 900 ₽"


def negative(*ratings: int | None) -> ItemState:
    latest = None if ratings == (None,) else tuple(rating for rating in ratings if rating is not None)
    reviews = ReviewFacts(total=10, with_photo=1, with_video=1, latest_ratings=latest)
    return states(facts(reviews=reviews))["negative_recent"]


def test_a_low_rating_among_the_latest_five_raises_the_warning() -> None:
    for ratings in ((5, 5, 5, 5, 3), (1, 5, 5, 5, 5), (5, 2, 5, 4, 5)):
        state = negative(*ratings)
        assert state.done is False and state.detail is not None and state.detail.startswith("есть: 1 из 5")


def test_four_and_five_stars_are_not_negative() -> None:
    state = negative(5, 4, 5, 4, 4)

    assert (state.done, state.detail) == (True, "нет (из 5)")


def test_fewer_than_five_reviews_are_judged_as_they_are() -> None:
    assert negative(5, 1).detail == "есть: 1 из 2"
    assert negative(5, 5).done is True


def test_no_reviews_or_no_record_is_unknown_not_clean() -> None:
    assert (negative().done, negative().detail) == (None, "отзывов нет")
    assert (negative(None).done, negative(None).detail) == (None, "ещё не считали")
    assert states(facts(reviews=None))["negative_recent"].done is None


@dataclass(frozen=True)
class Rated:
    created_at: datetime
    rating: int


@dataclass(frozen=True)
class Counts:
    total: int
    with_photo: int | None
    with_video: int | None
    latest: tuple[Rated, ...] | None


def test_the_latest_five_are_taken_across_the_whole_glue_by_date() -> None:
    day = datetime(2026, 11, 1, tzinfo=UTC)
    cards = [card(article="1", imt_id=7), card(article="2", imt_id=7), card(article="3", imt_id=8)]
    totals = {
        "1": Counts(6, None, None, tuple(Rated(day + timedelta(days=n), 5) for n in (1, 3, 5, 7, 9))),
        "2": Counts(2, None, None, (Rated(day + timedelta(days=8), 1), Rated(day + timedelta(days=2), 2))),
        "3": Counts(0, None, None, ()),
    }

    result = ChecklistService._card_reviews(cards, totals)

    assert result["1"].latest_ratings == (5, 1, 5, 5, 5)
    assert result["2"].latest_ratings == result["1"].latest_ratings
    assert result["3"].latest_ratings == ()


def test_a_glue_nobody_recorded_yet_stays_unknown() -> None:
    cards = [card(article="1", imt_id=7), card(article="2", imt_id=7)]
    totals = {"1": Counts(3, None, None, None), "2": Counts(1, None, None, None)}

    assert ChecklistService._card_reviews(cards, totals)["1"].latest_ratings is None


def row_of(article_facts: ArticleFacts) -> ChecklistRow:
    return ChecklistRow(
        article="100",
        vendor_code="SKU-100",
        barcode="1",
        title="Пила",
        photo_url="",
        subject_name="",
        card_created_at=None,
        stock=50,
        items=tuple(evaluate(article_facts, THRESHOLDS)),
        comment="",
    )


def test_the_warning_is_named_in_the_note_but_does_not_spoil_readiness() -> None:
    reviews = ReviewFacts(total=10, with_photo=1, with_video=1, latest_ratings=(5, 1, 5, 2, 5))

    row = row_of(facts(reviews=reviews))

    assert (row.done, row.total, row.ready) == (10, 10, True)
    assert row.note == "Внимание: Негатив в 5 свежих (есть: 2 из 5)"


def test_unknown_negative_is_not_listed_among_missing_data() -> None:
    row = row_of(facts())
    assert row.note == "Всё выполнено"
    assert row_of(facts(reviews=None)).note == "Нет данных: Отзывы есть, Отзывы с фото, Отзывы с видео"


def docs(**overrides: Any) -> DocumentFacts:
    values: dict[str, Any] = {"items": 1, "verified": 1, "rejected": 0, "rejection_reasons": (), "excluded": False}
    values.update(overrides)
    return DocumentFacts(**values)


def documents_state(documents: DocumentFacts | None, characteristic_ids: frozenset[int] = frozenset({1})) -> ItemState:
    return states(facts(card=card(documents=documents, characteristic_ids=characteristic_ids)))["documents"]


def test_accepted_documents_make_the_item_green() -> None:
    state = documents_state(docs(items=2, verified=2))

    assert (state.done, state.detail) == (True, "приняты (2)")


def test_a_rejected_document_names_the_reason_as_wb_gave_it() -> None:
    state = documents_state(docs(items=2, verified=1, rejected=1, rejection_reasons=("document_not_found",)))

    assert (state.done, state.detail) == (False, "отклонены: нет в реестре")
    assert documents_state(docs(verified=0, rejected=1, rejection_reasons=("new_reason",))).detail == (
        "отклонены: new_reason"
    )
    assert documents_state(docs(verified=0, rejected=1)).detail == "отклонены"


def test_no_documents_with_a_declaration_number_means_not_uploaded() -> None:
    state = documents_state(docs(items=0, verified=0), frozenset({1, 15001135}))

    assert (state.done, state.detail) == (False, "не загружены")


def test_no_documents_and_no_number_means_the_category_needs_none() -> None:
    """Решение от 22.10: фонари, фитолампы, отпариватели без номера декларации — не проблема."""
    state = documents_state(docs(items=0, verified=0))

    assert (state.done, state.detail) == (True, "не требуются")


def test_the_sellers_exclusion_overrides_everything() -> None:
    assert documents_state(docs(items=0, verified=0, excluded=True), frozenset({15001135})).done is True


def test_documents_still_on_review_are_unknown_and_unread_ones_too() -> None:
    assert (documents_state(docs(items=2, verified=1)).done, documents_state(docs(items=2, verified=1)).detail) == (
        None,
        "на проверке",
    )
    assert (documents_state(None).done, documents_state(None).detail) == (None, "ещё не считали")
