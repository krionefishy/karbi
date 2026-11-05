import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ReviewMirror, StockMirror
from backend.modules.wb_core.domain import Article, ReviewFact, StockFact
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.application import GroupActionError, ProductNotFoundError, ReviewSyncService
from backend.modules.wb_reviews.application.groups import MAX_DUMMIES, GroupCandidate, Placement, resolve_groups
from backend.modules.wb_reviews.domain import DailyRatings, GroupOverride
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)

# Склейка 900: 101 — товар с остатком, 102 и 201 — болванки (у 201 своё название, но склейка та же).
# 103 — неприклеенная болванка с названием товара 101. 301 — другой товар.
CANDIDATES = [
    GroupCandidate("101", 900, 22, 50, "Шуруповерт для дома", stock=40),
    GroupCandidate("102", 900, 0, 70, "шуруповёрт  для дома", stock=0),
    GroupCandidate("103", 555, 1, 3, "Шуруповерт для дома!", stock=0),
    GroupCandidate("201", 900, 0, 10, "Компрессор", stock=0),
    GroupCandidate("301", None, 12, 5, "Пылесос", stock=7),
]


def override(article: str, kind: str, main: str | None = None, *, minutes: int = 0) -> GroupOverride:
    return GroupOverride(article, kind, main, T0 + timedelta(minutes=minutes))


def test_a_card_with_stock_is_the_main_and_zero_stock_cards_are_its_dummies() -> None:
    placements = resolve_groups(CANDIDATES, {})

    assert placements["101"] == Placement("g:101", "main", False)
    assert placements["102"] == Placement("g:101", "member", False), "отзывов больше, но остатка нет"
    assert placements["103"] == Placement("g:101", "member", False), "не приклеена, но название товара"
    assert placements["201"] == Placement("g:101", "member", False), "название другое, но склейка товара"
    assert placements["301"] == Placement(None, None, False)


def test_two_products_under_one_name_keep_their_own_rows() -> None:
    """Саниев: «зелёный» и «дрель» в одной склейке и с одним названием — это два товара."""
    name = "Шуруповерт аккумуляторный мощный ударный"
    placements = resolve_groups(
        [
            GroupCandidate("1", 900, 12, 0, name, 2055, "Шуруповерт фирменный зеленый"),
            GroupCandidate("2", 900, 14, 0, name, 1804, "Шуруповерт аккумуляторный дрель"),
            GroupCandidate("3", 900, 1, 0, name, 0, "Шуруповерт фирменный зеленый фото"),
            GroupCandidate("4", 900, 0, 0, "Шуруповерт для дома", 0, "шуруповёрт7"),
        ],
        {},
    )

    assert placements["1"].role == "main"
    assert placements["2"] == Placement(None, None, False), "второй товар своей строкой, а не болванкой"
    assert placements["3"].group_id == placements["1"].group_id, "артикул «… фото» — к своему товару"
    assert placements["4"].group_id == placements["1"].group_id, "подсказок нет — к большему остатку"


def test_the_name_picks_the_product_inside_a_shared_wb_card() -> None:
    placements = resolve_groups(
        [
            GroupCandidate("1", 900, 22, 0, "Шуруповерт мощный ударный", 1652),
            GroupCandidate("2", 900, 14, 0, "Шуруповерт для дома", 475),
            GroupCandidate("3", 900, 0, 0, "шуруповерт для дома", 0, "шуруповёрт3"),
        ],
        {},
    )

    assert placements["3"].group_id == placements["2"].group_id


def test_loose_dummies_find_the_product_by_vendor_code_name_prefix_and_neighbours() -> None:
    subject = "Компрессоры"
    cordless = "Автомобильный насос компрессор беспроводной"
    placements = resolve_groups(
        [
            GroupCandidate(
                "10", 1, 12, 0, "Автомобильный насос компрессор беспроводной с насадками", 900, "Компрессор", subject
            ),
            GroupCandidate(
                "11", 2, 16, 0, "Автомобильный насос компрессор с насадками", 600, "Компрессор new", subject
            ),
            GroupCandidate("12", 3, 0, 0, cordless, 0, "Компрессоры3", subject),
            GroupCandidate("13", 4, 1, 0, "Насос", 0, "Компрессор new фото", subject),
            GroupCandidate("14", 1, 0, 0, "Компрессор от прикуривателя", 0, "4cacsx66", subject),
            GroupCandidate("15", 5, 0, 0, "Компрессор от прикуривателя", 0, "2oke5r72", subject),
            GroupCandidate("16", 6, 0, 0, "насос", 0, "сани1", subject),
        ],
        {},
    )

    def group_of(article: str) -> str | None:
        return placements[article].group_id

    assert group_of("12") == group_of("10"), "название — начало названия ровно одного товара"
    assert group_of("13") == group_of("11"), "артикул продавца"
    assert group_of("15") == group_of("10"), "туда же, куда приклеенная болванка с тем же названием"
    assert placements["16"] == Placement(None, None, False), "общее название и чужой артикул — не угадываем"


def test_a_product_never_gets_more_than_thirty_dummies() -> None:
    real = GroupCandidate("1", 900, 22, 0, "Пила", 10, "Пила")
    glued = [GroupCandidate(f"g{index:02}", 900, 0, 0, "Пила", 0, f"пила{index}") for index in range(25)]
    loose = [GroupCandidate(f"l{index:02}", 1000 + index, 0, 0, "Пила", 0, f"x{index}") for index in range(10)]

    placements = resolve_groups([real, *glued, *loose], {})

    members = [article for article, placement in placements.items() if placement.role == "member"]
    assert len(members) == MAX_DUMMIES
    assert all(placements[dummy.article].group_id == "g:1" for dummy in glued), "склейка — подсказка сильнее названия"


def test_without_stock_data_a_card_with_photos_is_the_main() -> None:
    placements = resolve_groups(
        [GroupCandidate("1", 5, 14, 0, "Пила"), GroupCandidate("2", 5, 0, 0, "Пила"), GroupCandidate("3", 5, 1, 0, "")],
        {},
    )

    assert placements["1"].role == "main"
    assert placements["2"].group_id == placements["3"].group_id == "g:1"


def test_a_pinned_main_wins_over_the_automatic_choice() -> None:
    placements = resolve_groups(CANDIDATES, {"102": override("102", "main")})

    assert placements["102"] == Placement("g:101", "main", True)
    assert placements["101"] == Placement("g:101", "member", False)


def test_the_latest_pin_wins_when_two_mains_end_up_in_one_group() -> None:
    overrides = {"102": override("102", "main"), "103": override("103", "main", minutes=5)}

    assert resolve_groups(CANDIDATES, overrides)["103"].role == "main"


def test_an_unglued_dummy_can_be_attached_to_the_main() -> None:
    placements = resolve_groups(CANDIDATES, {"201": override("201", "member", "101")})

    assert placements["201"] == Placement("g:101", "member", True)
    assert placements["101"].role == "main"


def test_attaching_to_an_attached_dummy_lands_in_its_group_without_looping() -> None:
    overrides = {"201": override("201", "member", "102"), "102": override("102", "member", "201")}

    placements = resolve_groups(CANDIDATES, overrides)

    assert placements["201"].group_id == placements["102"].group_id


def test_a_detached_card_stands_alone() -> None:
    placements = resolve_groups(CANDIDATES, {"103": override("103", "solo")})

    assert placements["103"] == Placement(None, None, True)
    assert placements["102"].group_id == "g:101"


class Sellers:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.seller_id = seller_id
        self.articles = [
            Article(uuid.uuid4(), seller_id, "101", "Шуруповерт", "Шуруповерт", imt_id=900, photo_count=22),
            Article(uuid.uuid4(), seller_id, "102", "шуруповёрт1", "Шуруповерт", imt_id=900, photo_count=0),
            Article(uuid.uuid4(), seller_id, "201", "Компрессор3", "Компрессор", imt_id=900, photo_count=0),
            Article(uuid.uuid4(), seller_id, "301", "Пылесос", "Пылесос", imt_id=None, photo_count=10),
        ]

    async def get(self, seller_id: uuid.UUID):
        return SimpleNamespace(id=seller_id) if seller_id == self.seller_id else None

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        return self.articles


class Reviews:
    def __init__(self, seller_id: uuid.UUID) -> None:
        stamp = datetime.now(UTC)
        self.rows = [
            DailyRatings(seller_id, "101", date(2026, 11, 3), (0, 0, 0, 1, 9), stamp),
            DailyRatings(seller_id, "101", date(2026, 11, 4), (0, 0, 0, 1, 10), stamp),
            DailyRatings(seller_id, "102", date(2026, 11, 4), (1, 0, 0, 0, 4), stamp),
            DailyRatings(seller_id, "201", date(2026, 11, 4), (0, 0, 0, 0, 2), stamp),
            DailyRatings(seller_id, "301", date(2026, 11, 4), (1, 0, 0, 0, 3), stamp),
        ]
        self.overrides: dict[str, GroupOverride] = {}

    async def history(self, seller_id: uuid.UUID, days: int) -> list[DailyRatings]:
        return self.rows

    async def group_overrides(self, seller_id: uuid.UUID) -> dict[str, GroupOverride]:
        return dict(self.overrides)

    async def save_group_override(
        self, seller_id: uuid.UUID, article: str, kind: str, main_article: str | None, *, now: datetime
    ) -> None:
        self.overrides[article] = GroupOverride(article, kind, main_article, now)

    async def delete_group_override(self, seller_id: uuid.UUID, article: str) -> None:
        self.overrides.pop(article, None)


class Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class Stocks:
    def __init__(self, stocks: dict[str, int]) -> None:
        self.stocks = stocks

    async def stock(self, seller_id: uuid.UUID, *, fresh_since: datetime) -> dict[str, StockFact]:
        return {article: StockFact(article, quantity, quantity, 1) for article, quantity in self.stocks.items()}


class Ratings:
    def __init__(self, ratings: dict[str, Decimal]) -> None:
        self.ratings = ratings

    async def totals(self, seller_id: uuid.UUID) -> dict[str, ReviewFact]:
        return {
            article: ReviewFact(article, (0, 0, 0, 0, 0), None, None, wb_rating=rating)
            for article, rating in self.ratings.items()
        }


def service(
    seller_id: uuid.UUID, reviews: Reviews, ratings: dict[str, Decimal], stocks: dict[str, int] | None = None
) -> ReviewSyncService:
    return ReviewSyncService(
        cast(AsyncSession, Session()),
        cast(SellerRepository, Sellers(seller_id)),
        cast(ReviewSyncRepository, reviews),
        cast(ReviewMirror, Ratings(ratings)),
        cast(StockMirror, Stocks(stocks or {})),
    )


async def test_history_carries_ratings_groups_and_group_totals() -> None:
    seller_id = uuid.uuid4()
    reviews = Reviews(seller_id)
    reviews.overrides["201"] = override("201", "member", "101")

    history = await service(seller_id, reviews, {"101": Decimal("4.90")}).history(seller_id, 90)
    products = {product.article: product for product in history.products}

    assert (products["101"].rating, products["101"].rating_source) == (4.9, "wb")
    assert (products["102"].rating, products["102"].rating_source) == (4.9, "group")
    assert (products["201"].rating, products["201"].rating_source) == (4.9, "group")
    assert (products["301"].rating, products["301"].rating_source) == (4.0, "reviews")
    assert (products["101"].group_role, products["101"].group_manual) == ("main", False)
    assert (products["201"].group_role, products["201"].group_manual) == ("member", True)
    assert products["101"].group_id == products["201"].group_id == "g:101"
    assert products["301"].group_id is None and products["301"].group_role is None
    assert products["101"].photo_count == 22
    assert products["101"].group_snapshots == [
        {"date": "2026-11-03", "ratings": {1: 0, 2: 0, 3: 0, 4: 1, 5: 9}},
        {"date": "2026-11-04", "ratings": {1: 1, 2: 0, 3: 0, 4: 1, 5: 16}},
    ]
    assert products["102"].group_snapshots == []


async def test_the_main_is_the_card_with_stock_in_the_mirror_whatever_its_photos() -> None:
    seller_id = uuid.uuid4()

    history = await service(seller_id, Reviews(seller_id), {}, {"102": 5}).history(seller_id, 90)
    roles = {product.article: product.group_role for product in history.products}

    assert roles == {"101": "member", "102": "main", "201": "member", "301": None}


async def test_a_card_without_any_rating_or_reviews_has_none() -> None:
    seller_id = uuid.uuid4()
    reviews = Reviews(seller_id)
    reviews.rows = []

    history = await service(seller_id, reviews, {}).history(seller_id, 90)

    assert {(product.rating, product.rating_source) for product in history.products} == {(None, None)}


async def test_pin_main_moves_the_pin_and_keeps_a_manual_attachment() -> None:
    seller_id = uuid.uuid4()
    reviews = Reviews(seller_id)
    reviews.overrides["201"] = override("201", "member", "101")
    reviews.overrides["101"] = override("101", "main")
    target = service(seller_id, reviews, {})

    await target.change_group(seller_id, "201", "pin_main", None)

    assert reviews.overrides["201"].kind == "main"
    assert reviews.overrides["201"].main_article == "101"
    assert "101" not in reviews.overrides
    history = await target.history(seller_id, 90)
    roles = {product.article: product.group_role for product in history.products}
    assert roles == {"101": "member", "102": "member", "201": "main", "301": None}


async def test_detach_and_reset() -> None:
    seller_id = uuid.uuid4()
    reviews = Reviews(seller_id)
    target = service(seller_id, reviews, {})

    await target.change_group(seller_id, "102", "detach", None)
    assert reviews.overrides["102"].kind == "solo"
    await target.change_group(seller_id, "102", "reset", None)
    assert "102" not in reviews.overrides


@pytest.mark.parametrize(
    ("article", "action", "main", "error"),
    [
        ("201", "attach", None, GroupActionError),
        ("201", "attach", "201", GroupActionError),
        ("201", "attach", "999", ProductNotFoundError),
        ("999", "detach", None, ProductNotFoundError),
        ("201", "explode", None, GroupActionError),
    ],
)
async def test_invalid_group_changes_are_rejected(article: str, action: str, main: str | None, error: type) -> None:
    seller_id = uuid.uuid4()
    reviews = Reviews(seller_id)

    with pytest.raises(error):
        await service(seller_id, reviews, {}).change_group(seller_id, article, action, main)
    assert reviews.overrides == {}


def test_a_chain_of_attachments_ends_in_one_group() -> None:
    overrides = {"301": override("301", "member", "201"), "201": override("201", "member", "102")}

    placements = resolve_groups(CANDIDATES, overrides)

    assert placements["301"].group_id == placements["201"].group_id == placements["101"].group_id == "g:101"
