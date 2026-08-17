import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres.models import (
    ArticleModel,
    CredentialModel,
    InboxEventModel,
    OutboxEventModel,
    SellerModel,
)


class SellerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_sellers(self) -> list[Seller]:
        counts = (
            select(ArticleModel.seller_id, func.count().label("count"))
            .where(ArticleModel.is_active)
            .group_by(ArticleModel.seller_id)
            .subquery()
        )
        rows = await self.session.execute(
            select(SellerModel, func.coalesce(counts.c.count, 0))
            .outerjoin(counts, counts.c.seller_id == SellerModel.id)
            .where(SellerModel.is_active)
            .order_by(SellerModel.created_at)
        )
        return [self._seller(model, int(count)) for model, count in rows.all()]

    async def get(self, seller_id: uuid.UUID) -> SellerModel | None:
        return await self.session.get(SellerModel, seller_id)

    async def get_credential(self, seller_id: uuid.UUID) -> CredentialModel | None:
        return await self.session.scalar(select(CredentialModel).where(CredentialModel.seller_id == seller_id))

    async def fingerprint_exists(self, fingerprint: str, *, excluding: uuid.UUID | None = None) -> bool:
        query = select(CredentialModel.id).where(CredentialModel.key_fingerprint == fingerprint)
        if excluding:
            query = query.where(CredentialModel.seller_id != excluding)
        return await self.session.scalar(query) is not None

    async def create(self, name: str, encrypted_key: str, fingerprint: str) -> SellerModel:
        seller = SellerModel(name=name, is_active=True, catalog_sync_status="queued")
        self.session.add(seller)
        await self.session.flush()
        self.session.add(
            CredentialModel(seller_id=seller.id, encrypted_api_key=encrypted_key, key_fingerprint=fingerprint)
        )
        return seller

    async def delete(self, seller_id: uuid.UUID) -> bool:
        seller = await self.get(seller_id)
        if seller is None:
            return False
        await self.session.execute(
            text("DELETE FROM wb_reviews.daily_review_counts WHERE seller_id = :seller_id"), {"seller_id": seller_id}
        )
        await self.session.execute(
            delete(OutboxEventModel).where(
                OutboxEventModel.aggregate_id == seller_id,
                OutboxEventModel.event_type == "WBCatalogSyncRequested",
                OutboxEventModel.published_at.is_(None),
            )
        )
        await self.session.delete(seller)
        return True

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        rows = await self.session.scalars(
            select(ArticleModel)
            .where(ArticleModel.seller_id == seller_id, ArticleModel.is_active)
            .order_by(ArticleModel.name, ArticleModel.article)
        )
        return [Article(row.id, row.seller_id, row.article, row.vendor_code, row.name) for row in rows]

    async def upsert_articles(self, seller_id: uuid.UUID, articles: list[dict[str, str]]) -> None:
        active_articles = [item["article"] for item in articles]
        if active_articles:
            await self.session.execute(
                update(ArticleModel)
                .where(ArticleModel.seller_id == seller_id, ArticleModel.article.not_in(active_articles))
                .values(is_active=False)
            )
        else:
            await self.session.execute(
                update(ArticleModel).where(ArticleModel.seller_id == seller_id).values(is_active=False)
            )
        for item in articles:
            statement = (
                insert(ArticleModel)
                .values(seller_id=seller_id, is_active=True, **item)
                .on_conflict_do_update(
                    constraint="uq_wb_core_articles_seller_article",
                    set_={
                        "name": item["name"],
                        "vendor_code": item["vendor_code"],
                        "is_active": True,
                        "updated_at": func.now(),
                    },
                )
            )
            await self.session.execute(statement)

    async def set_sync_status(self, seller_id: uuid.UUID, status: str, error: str | None = None) -> None:
        values: dict = {"catalog_sync_status": status, "catalog_sync_error": error}
        if status == "success":
            values["last_catalog_sync_at"] = datetime.now(UTC)
        await self.session.execute(update(SellerModel).where(SellerModel.id == seller_id).values(**values))

    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return await self.session.get(InboxEventModel, event_id) is not None

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.session.add(InboxEventModel(event_id=event_id, event_type=event_type))

    @staticmethod
    def _seller(model: SellerModel, count: int) -> Seller:
        return Seller(
            model.id, model.name, count, model.catalog_sync_status, model.last_catalog_sync_at, model.catalog_sync_error
        )
