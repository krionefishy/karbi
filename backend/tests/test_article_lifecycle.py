from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import CatalogCard
from backend.shared.settings import load_settings
from backend.storage.pg import Database


def card(article: str, **overrides) -> CatalogCard:
    return CatalogCard(
        article=article,
        vendor_code=f"SKU-{article}",
        name=f"Товар {article}",
        imt_id=int(overrides.pop("imt_id", 500)),
        **overrides,
    )


@pytest_asyncio.fixture
async def seller_repository() -> AsyncIterator[tuple[Database, SellerModel]]:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    seller = SellerModel(name="Lifecycle seller", is_active=True, catalog_sync_status="success")
    async with database.session() as session:
        session.add(seller)
        await session.commit()
    try:
        yield database, seller
    finally:
        async with database.session() as session:
            await session.execute(delete(ArticleModel).where(ArticleModel.seller_id == seller.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller.id))
            await session.commit()
        await database.disconnect()


async def states(database: Database, seller_id) -> dict[str, str]:
    async with database.session() as session:
        return {item.article: item.state for item in await SellerRepository(session).list_articles(seller_id)}


@pytest.mark.asyncio
async def test_archived_cards_are_marked_archived_not_deleted(seller_repository) -> None:
    database, seller = seller_repository
    async with database.session() as session:
        repository = SellerRepository(session)
        await repository.upsert_catalog(seller.id, active=[card("1"), card("2")], archived=[], archived_available=True)
        await session.commit()

    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1")], archived=[card("2")], archived_available=True
        )
        await session.commit()

    assert await states(database, seller.id) == {"1": "active", "2": "archived"}


@pytest.mark.asyncio
async def test_a_card_missing_from_both_lists_becomes_feedback_only(seller_repository) -> None:
    database, seller = seller_repository
    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1"), card("2")], archived=[], archived_available=True
        )
        await session.commit()

    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1")], archived=[], archived_available=True
        )
        await session.commit()

    assert await states(database, seller.id) == {"1": "active", "2": "feedback_only"}


@pytest.mark.asyncio
async def test_an_unreadable_trash_never_demotes_archived_cards(seller_repository) -> None:
    database, seller = seller_repository
    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1")], archived=[card("2")], archived_available=True
        )
        await session.commit()

    async with database.session() as session:
        # WB refused the trash endpoint: we cannot tell archived from gone, so
        # the archived card must keep its state.
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1")], archived=[], archived_available=False
        )
        await session.commit()

    assert await states(database, seller.id) == {"1": "active", "2": "archived"}


@pytest.mark.asyncio
async def test_feedback_articles_never_overwrite_catalog_state(seller_repository) -> None:
    database, seller = seller_repository
    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id, active=[card("1")], archived=[card("2")], archived_available=True
        )
        await session.commit()

    async with database.session() as session:
        # The review sync sees all three in feedbacks. It may register the new
        # one, but must not resurrect the archived card as active.
        await SellerRepository(session).ensure_feedback_articles(seller.id, [card("1"), card("2"), card("3")])
        await session.commit()

    assert await states(database, seller.id) == {"1": "active", "2": "archived", "3": "feedback_only"}


@pytest.mark.asyncio
async def test_catalog_upsert_stores_the_identity_fields(seller_repository) -> None:
    database, seller = seller_repository
    async with database.session() as session:
        await SellerRepository(session).upsert_catalog(
            seller.id,
            active=[
                CatalogCard(
                    article="9",
                    vendor_code="SKU-9",
                    name="Шуруповерт",
                    imt_id=1460090445,
                    brand="Karbi",
                    subject_id=42,
                    subject_name="Шуруповерты",
                    photo_url="https://basket.wb.ru/9.jpg",
                    sizes=[{"chrt_id": 3, "tech_size": "0", "skus": ["2000000000009"]}],
                )
            ],
            archived=[],
            archived_available=True,
        )
        await session.commit()

    async with database.session() as session:
        [article] = await SellerRepository(session).list_articles(seller.id)

    assert article.imt_id == 1460090445
    assert article.brand == "Karbi"
    assert article.subject_name == "Шуруповерты"
    assert article.photo_url == "https://basket.wb.ru/9.jpg"
