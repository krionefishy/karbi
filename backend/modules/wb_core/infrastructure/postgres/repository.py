import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, delete, func, select, update
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
from backend.modules.wb_core.infrastructure.wb import CatalogCard

_UPSERT_CHUNK = 500


class SellerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_sellers(self, *, include_archived: bool = False) -> list[Seller]:
        counts = (
            select(ArticleModel.seller_id, func.count().label("count"))
            .where(ArticleModel.state == "active")
            .group_by(ArticleModel.seller_id)
            .subquery()
        )
        query = (
            select(SellerModel, func.coalesce(counts.c.count, 0))
            .outerjoin(counts, counts.c.seller_id == SellerModel.id)
            .order_by(SellerModel.created_at)
        )
        if not include_archived:
            query = query.where(SellerModel.archived_at.is_(None))
        rows = await self.session.execute(query)
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
        seller = SellerModel(name=name, catalog_sync_status="queued")
        self.session.add(seller)
        await self.session.flush()
        self.session.add(
            CredentialModel(seller_id=seller.id, encrypted_api_key=encrypted_key, key_fingerprint=fingerprint)
        )
        return seller

    async def archive(self, seller_id: uuid.UUID) -> bool:
        """Take the seller out of service without losing anything collected.

        The API key goes away with him: nothing may call Wildberries for an
        archived seller, and a lingering credential would also keep its
        fingerprint reserved, so re-adding the same key would be refused as a
        duplicate with no way to see why.
        """
        seller = await self.get(seller_id)
        if seller is None or seller.archived_at is not None:
            return False
        seller.archived_at = datetime.now(UTC)
        await self.session.execute(delete(CredentialModel).where(CredentialModel.seller_id == seller_id))
        await self._drop_pending_sync_events(seller_id)
        return True

    async def restore(self, seller_id: uuid.UUID, encrypted_key: str, fingerprint: str) -> bool:
        seller = await self.get(seller_id)
        if seller is None or seller.archived_at is None:
            return False
        seller.archived_at = None
        seller.catalog_sync_status = "queued"
        seller.catalog_sync_error = None
        self.session.add(
            CredentialModel(seller_id=seller_id, encrypted_api_key=encrypted_key, key_fingerprint=fingerprint)
        )
        return True

    async def delete(self, seller_id: uuid.UUID) -> bool:
        """Erase the seller himself; what automations stored is their own to drop."""
        seller = await self.get(seller_id)
        if seller is None:
            return False
        await self._drop_pending_sync_events(seller_id)
        await self.session.delete(seller)
        return True

    async def _drop_pending_sync_events(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(OutboxEventModel).where(
                OutboxEventModel.aggregate_id == seller_id,
                OutboxEventModel.event_type == "WBCatalogSyncRequested",
                OutboxEventModel.published_at.is_(None),
            )
        )

    async def list_articles(self, seller_id: uuid.UUID) -> list[Article]:
        """Everything we know about the seller, archived cards included.

        Archived товары keep their review history, so hiding them would lose the
        very numbers the timeline exists to show.
        """
        rows = await self.session.scalars(
            select(ArticleModel)
            .where(ArticleModel.seller_id == seller_id)
            .order_by(
                case({"active": 0, "archived": 1}, value=ArticleModel.state, else_=2),
                ArticleModel.name,
                ArticleModel.article,
            )
        )
        return [self._article(row) for row in rows]

    async def list_chrt_articles(self, seller_id: uuid.UUID) -> dict[int, str]:
        """Size id → article, as the catalog sync stored it.

        The FBS stock method is asked by `chrtId`: one size can carry several
        barcodes, so barcodes made the mapping ambiguous and cost an extra step.
        """
        rows = await self.session.scalars(
            select(ArticleModel).where(ArticleModel.seller_id == seller_id, ArticleModel.state != "archived")
        )
        collected: dict[int, str] = {}
        for row in rows:
            for size in row.sizes or []:
                if not isinstance(size, dict):
                    continue
                chrt_id = size.get("chrt_id")
                if isinstance(chrt_id, int):
                    collected[chrt_id] = row.article
        return collected

    async def list_barcode_sizes(self, seller_id: uuid.UUID) -> dict[str, tuple[int, str]]:
        """Barcode → (size id, article) for the seller's live cards.

        The FBS stock method is asked by `chrtId`, but 1C knows a good by its
        barcode, so the mapping between the two has to start here. One size can
        carry several barcodes; the same barcode never belongs to two sizes of
        one seller, and if WB ever returns that, the later one is a data error
        the caller must see rather than a silent overwrite.
        """
        rows = await self.session.scalars(
            select(ArticleModel).where(ArticleModel.seller_id == seller_id, ArticleModel.state != "archived")
        )
        collected: dict[str, tuple[int, str]] = {}
        for row in rows:
            for size in row.sizes or []:
                if not isinstance(size, dict):
                    continue
                chrt_id = size.get("chrt_id")
                if not isinstance(chrt_id, int):
                    continue
                for sku in size.get("skus") or []:
                    barcode = str(sku).strip()
                    if barcode:
                        collected.setdefault(barcode, (chrt_id, row.article))
        return collected

    async def upsert_catalog(
        self,
        seller_id: uuid.UUID,
        *,
        active: Sequence[CatalogCard],
        archived: Sequence[CatalogCard],
        archived_available: bool,
    ) -> None:
        await self._upsert_cards(seller_id, active, "active")
        await self._upsert_cards(seller_id, archived, "archived")
        known = [card.article for card in active] + [card.article for card in archived]
        # Anything the account no longer lists is not "inactive" — it is a card we
        # can still see in feedbacks but can no longer resolve through the catalog.
        demote = update(ArticleModel).where(ArticleModel.seller_id == seller_id)
        if known:
            demote = demote.where(ArticleModel.article.not_in(known))
        if not archived_available:
            demote = demote.where(ArticleModel.state == "active")
        await self.session.execute(demote.values(state="feedback_only", updated_at=func.now()))

    async def ensure_feedback_articles(self, seller_id: uuid.UUID, cards: Sequence[CatalogCard]) -> None:
        """Register articles seen only in feedbacks without touching catalog state."""
        if not cards:
            return
        for chunk in self._chunks([self._row(seller_id, card, "feedback_only") for card in cards]):
            statement = insert(ArticleModel).values(chunk)
            await self.session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_wb_core_articles_seller_article",
                    set_={
                        "name": statement.excluded.name,
                        "vendor_code": statement.excluded.vendor_code,
                        "imt_id": func.coalesce(statement.excluded.imt_id, ArticleModel.imt_id),
                        "updated_at": func.now(),
                    },
                    where=ArticleModel.state == "feedback_only",
                )
            )

    async def _upsert_cards(self, seller_id: uuid.UUID, cards: Sequence[CatalogCard], state: str) -> None:
        if not cards:
            return
        for chunk in self._chunks([self._row(seller_id, card, state) for card in cards]):
            statement = insert(ArticleModel).values(chunk)
            await self.session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_wb_core_articles_seller_article",
                    set_={
                        "name": statement.excluded.name,
                        "vendor_code": statement.excluded.vendor_code,
                        "imt_id": statement.excluded.imt_id,
                        "brand": statement.excluded.brand,
                        "subject_id": statement.excluded.subject_id,
                        "subject_name": statement.excluded.subject_name,
                        "photo_url": statement.excluded.photo_url,
                        "sizes": statement.excluded.sizes,
                        "state": statement.excluded.state,
                        "updated_at": func.now(),
                    },
                )
            )

    @staticmethod
    def _row(seller_id: uuid.UUID, card: CatalogCard, state: str) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "seller_id": seller_id,
            "article": card.article,
            "vendor_code": card.vendor_code,
            "name": card.name,
            "imt_id": card.imt_id,
            "brand": card.brand,
            "subject_id": card.subject_id,
            "subject_name": card.subject_name,
            "photo_url": card.photo_url,
            "sizes": card.sizes,
            "state": state,
        }

    @staticmethod
    def _chunks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        return [rows[offset : offset + _UPSERT_CHUNK] for offset in range(0, len(rows), _UPSERT_CHUNK)]

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
            model.id,
            model.name,
            count,
            model.catalog_sync_status,
            model.last_catalog_sync_at,
            model.catalog_sync_error,
            model.archived_at,
        )

    @staticmethod
    def _article(model: ArticleModel) -> Article:
        return Article(
            id=model.id,
            seller_id=model.seller_id,
            article=model.article,
            vendor_code=model.vendor_code,
            name=model.name,
            imt_id=model.imt_id,
            brand=model.brand,
            subject_id=model.subject_id,
            subject_name=model.subject_name,
            photo_url=model.photo_url,
            state=model.state,
        )
