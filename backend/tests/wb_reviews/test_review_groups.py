import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ReviewMirror
from backend.modules.wb_core.domain import Article, ReviewFact
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.application import GroupActionError, ProductNotFoundError, ReviewSyncService
from backend.modules.wb_reviews.application.groups import GroupCandidate, Placement, resolve_groups
from backend.modules.wb_reviews.domain import DailyRatings, GroupOverride
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)

# Склейка 900: основной 101 (22 фото) и приклеенные болванки 102, 103 без фото.
# 201 — неприклеенная болванка со своим imtID, 301 — другой товар.
CANDIDATES = [
    GroupCandidate("101", 900, 22, 50),
    GroupCandidate("102", 900, 0, 70),
    GroupCandidate("103", 900, 1, 3),
    GroupCandidate("201", 777, 0, 10),
    GroupCandidate("301", None, 12, 5),
]


def override(article: str, kind: str, main: str | None = None, *, minutes: int = 0) -> GroupOverride:
    return GroupOverride(article, kind, main, T0 + timedelta(minutes=minutes))


def test_auto_groups_follow_wb_cards_and_pick_the_richest_card_as_main() -> None:
    placements = resolve_groups(CANDIDATES, {})

    assert placements["101"] == Placement("imt:900", "main", False)
    assert placements["102"] == Placement("imt:900", "member", False)
    assert placements["103"] == Placement("imt:900", "member", False)
    assert placements["201"] == Placement(None, None, False)
    assert placements["301"] == Placement(None, None, False)


def test_auto_main_breaks_photo_ties_by_reviews_then_by_the_older_article() -> None:
    tied = [GroupCandidate("11", 5, 3, 1), GroupCandidate("12", 5, 3, 9), GroupCandidate("10", 5, 3, 9)]

    assert resolve_groups(tied, {})["10"].role == "main"


def test_a_pinned_main_wins_over_the_automatic_choice() -> None:
    placements = resolve_groups(CANDIDATES, {"102": override("102", "main")})

    assert placements["102"] == Placement("imt:900", "main", True)
    assert placements["101"] == Placement("imt:900", "member", False)


def test_the_latest_pin_wins_when_two_mains_end_up_in_one_group() -> None:
    overrides = {"102": override("102", "main"), "103": override("103", "main", minutes=5)}

    assert resolve_groups(CANDIDATES, overrides)["103"].role == "main"


def test_an_unglued_dummy_can_be_attached_to_the_main() -> None:
    placements = resolve_groups(CANDIDATES, {"201": override("201", "member", "101")})

    assert placements["201"] == Placement("imt:900", "member", True)
    assert placements["101"].role == "main"


def test_attaching_to_an_attached_dummy_lands_in_its_group_without_looping() -> None:
    overrides = {"201": override("201", "member", "102"), "102": override("102", "member", "201")}

    placements = resolve_groups(CANDIDATES, overrides)

    assert placements["201"].group_id == placements["102"].group_id


def test_a_detached_card_stands_alone() -> None:
    placements = resolve_groups(CANDIDATES, {"103": override("103", "solo")})

    assert placements["103"] == Placement(None, None, True)
    assert placements["102"].group_id == "imt:900"


class Sellers:
    def __init__(self, seller_id: uuid.UUID) -> None:
        self.seller_id = seller_id
        self.articles = [
            Article(uuid.uuid4(), seller_id, "101", "Шуруповерт", "Шуруповерт", imt_id=900, photo_count=22),
            Article(uuid.uuid4(), seller_id, "102", "шуруповёрт1", "Шуруповерт", imt_id=900, photo_count=0),
            Article(uuid.uuid4(), seller_id, "201", "Компрессор3", "Компрессор", imt_id=777, photo_count=0),
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


class Ratings:
    def __init__(self, ratings: dict[str, Decimal]) -> None:
        self.ratings = ratings

    async def totals(self, seller_id: uuid.UUID) -> dict[str, ReviewFact]:
        return {
            article: ReviewFact(article, (0, 0, 0, 0, 0), None, None, wb_rating=rating)
            for article, rating in self.ratings.items()
        }


def service(seller_id: uuid.UUID, reviews: Reviews, ratings: dict[str, Decimal]) -> ReviewSyncService:
    return ReviewSyncService(
        cast(AsyncSession, Session()),
        cast(SellerRepository, Sellers(seller_id)),
        cast(ReviewSyncRepository, reviews),
        cast(ReviewMirror, Ratings(ratings)),
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
    assert products["101"].group_id == products["201"].group_id == "imt:900"
    assert products["301"].group_id is None and products["301"].group_role is None
    assert products["101"].photo_count == 22
    assert products["101"].group_snapshots == [
        {"date": "2026-11-03", "ratings": {1: 0, 2: 0, 3: 0, 4: 1, 5: 9}},
        {"date": "2026-11-04", "ratings": {1: 1, 2: 0, 3: 0, 4: 1, 5: 16}},
    ]
    assert products["102"].group_snapshots == []


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

    assert placements["301"].group_id == placements["201"].group_id == placements["101"].group_id == "imt:900"
