import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_checklist.domain import CardFacts, PriceFacts, SubjectCharacteristic
from backend.modules.wb_card_checklist.infrastructure.postgres.models import (
    CardFactsModel,
    CommentModel,
    PriceFactsModel,
    RefreshRequestModel,
    SubjectCharacteristicsModel,
    TrackedSellerModel,
)

_CHUNK = 500


class ChecklistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- membership -------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        """Everything this automation holds about the seller, comments included.

        The subject directory stays: it is WB's, shared by every seller.
        """
        for model in (CardFactsModel, PriceFactsModel, CommentModel, RefreshRequestModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def still_tracked(self, seller_id: uuid.UUID) -> bool:
        """Whether the seller is still enrolled at write time, locking the row.

        A detach can land while WB is being read; FOR SHARE makes the purge
        wait for this transaction or makes this one see the row gone, so the
        write never resurrects what the purge just dropped.
        """
        found = await self.session.scalar(
            select(TrackedSellerModel.seller_id)
            .where(TrackedSellerModel.seller_id == seller_id)
            .with_for_update(read=True)
        )
        return found is not None

    # --- collection schedule ---------------------------------------------

    async def sellers_due(self, since: datetime, *, retry_after: datetime) -> list[uuid.UUID]:
        """Enrolled sellers not collected since `since` and not tried since `retry_after`."""
        rows = await self.session.scalars(
            select(TrackedSellerModel.seller_id)
            .where(
                (TrackedSellerModel.collected_at.is_(None)) | (TrackedSellerModel.collected_at < since),
                (TrackedSellerModel.attempted_at.is_(None)) | (TrackedSellerModel.attempted_at < retry_after),
            )
            .order_by(TrackedSellerModel.enrolled_at)
        )
        return list(rows)

    async def record_attempt(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(attempted_at=datetime.now(UTC))
        )

    async def finish_collection(self, seller_id: uuid.UUID, warning: str | None) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collected_at=datetime.now(UTC), collection_error=warning[:1000] if warning else None)
        )

    async def fail_collection(self, seller_id: uuid.UUID, error: str) -> None:
        """Keep the previous collection's data and its date; only say what went wrong."""
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collection_error=error[:1000])
        )

    async def collection_summary(self, seller_ids: set[uuid.UUID]) -> tuple[datetime | None, int]:
        """Last successful collection among these sellers, and how many carry an error."""
        if not seller_ids:
            return None, 0
        row = (
            await self.session.execute(
                select(
                    func.max(TrackedSellerModel.collected_at),
                    func.count().filter(TrackedSellerModel.collection_error.is_not(None)),
                ).where(TrackedSellerModel.seller_id.in_(seller_ids))
            )
        ).one()
        return row[0], int(row[1] or 0)

    # --- card facts -------------------------------------------------------

    async def replace_cards(self, seller_id: uuid.UUID, cards: Sequence[CardFacts]) -> None:
        await self.session.execute(delete(CardFactsModel).where(CardFactsModel.seller_id == seller_id))
        now = datetime.now(UTC)
        # WB не повторяет nmID в выдаче, но если повторит — поздняя строка
        # выиграет, а не уронит вставку конфликтом первичного ключа.
        unique = {card.article: card for card in cards}
        rows = [self._card_row(seller_id, card, now) for card in unique.values()]
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(CardFactsModel).values(rows[offset : offset + _CHUNK]))

    async def card_facts(self, seller_id: uuid.UUID) -> list[CardFacts]:
        rows = await self.session.scalars(
            select(CardFactsModel).where(CardFactsModel.seller_id == seller_id).order_by(CardFactsModel.article)
        )
        return [self._card(row) for row in rows]

    async def replace_prices(self, seller_id: uuid.UUID, prices: Sequence[PriceFacts]) -> None:
        await self.session.execute(delete(PriceFactsModel).where(PriceFactsModel.seller_id == seller_id))
        now = datetime.now(UTC)
        unique = {price.article: price for price in prices}
        rows = [
            {
                "seller_id": seller_id,
                "article": price.article,
                "price": price.price,
                "discounted_price": price.discounted_price,
                "discount": price.discount,
                "club_discount": price.club_discount,
                "club_discounted_price": price.club_discounted_price,
                "collected_at": now,
            }
            for price in unique.values()
        ]
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(PriceFactsModel).values(rows[offset : offset + _CHUNK]))

    async def prices(self, seller_id: uuid.UUID) -> dict[str, PriceFacts]:
        rows = await self.session.scalars(select(PriceFactsModel).where(PriceFactsModel.seller_id == seller_id))
        return {
            row.article: PriceFacts(
                article=row.article,
                price=float(row.price),
                discounted_price=float(row.discounted_price),
                discount=row.discount,
                club_discount=row.club_discount,
                club_discounted_price=float(row.club_discounted_price)
                if row.club_discounted_price is not None
                else None,
            )
            for row in rows
        }

    # --- subject directory -----------------------------------------------

    async def stale_subjects(self, subject_ids: Iterable[int], fresh_after: datetime) -> set[int]:
        wanted = set(subject_ids)
        if not wanted:
            return set()
        rows = await self.session.execute(
            select(SubjectCharacteristicsModel.subject_id, SubjectCharacteristicsModel.fetched_at).where(
                SubjectCharacteristicsModel.subject_id.in_(wanted)
            )
        )
        fresh = {subject_id for subject_id, fetched_at in rows.all() if fetched_at >= fresh_after}
        return wanted - fresh

    async def save_subject(self, subject_id: int, characteristics: Sequence[SubjectCharacteristic]) -> None:
        payload = [
            {
                "id": item.charc_id,
                "name": item.name,
                "required": item.required,
                "popular": item.popular,
                "named_field": item.named_field,
            }
            for item in characteristics
        ]
        statement = insert(SubjectCharacteristicsModel).values(
            subject_id=subject_id, characteristics=payload, fetched_at=datetime.now(UTC)
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["subject_id"],
                set_={
                    "characteristics": statement.excluded.characteristics,
                    "fetched_at": statement.excluded.fetched_at,
                },
            )
        )

    async def subject_characteristics(self, subject_ids: Iterable[int]) -> dict[int, tuple[SubjectCharacteristic, ...]]:
        wanted = set(subject_ids)
        if not wanted:
            return {}
        rows = await self.session.scalars(
            select(SubjectCharacteristicsModel).where(SubjectCharacteristicsModel.subject_id.in_(wanted))
        )
        return {
            row.subject_id: tuple(
                SubjectCharacteristic(
                    charc_id=int(item["id"]),
                    name=str(item.get("name") or ""),
                    required=bool(item.get("required")),
                    popular=bool(item.get("popular")),
                    named_field=bool(item.get("named_field")),
                )
                for item in row.characteristics or []
                if isinstance(item, dict) and "id" in item
            )
            for row in rows
        }

    # --- comments --------------------------------------------------------

    async def comments(self, seller_id: uuid.UUID) -> dict[str, str]:
        rows = await self.session.scalars(select(CommentModel).where(CommentModel.seller_id == seller_id))
        return {row.article: row.text for row in rows}

    async def set_comment(self, seller_id: uuid.UUID, article: str, text: str, updated_by: uuid.UUID | None) -> None:
        if not text.strip():
            await self.session.execute(
                delete(CommentModel).where(CommentModel.seller_id == seller_id, CommentModel.article == article)
            )
            return
        statement = insert(CommentModel).values(
            seller_id=seller_id, article=article, text=text, updated_by=updated_by, updated_at=datetime.now(UTC)
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "article"],
                set_={
                    "text": statement.excluded.text,
                    "updated_by": statement.excluded.updated_by,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    # --- manual refresh --------------------------------------------------

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequestModel:
        """Ask for an out-of-schedule collection, or hand back the one already waiting."""
        pending = await self.active_refresh(seller_id)
        if pending is not None:
            return pending
        request = RefreshRequestModel(seller_id=seller_id, requested_by=requested_by)
        self.session.add(request)
        await self.session.flush()
        return request

    async def active_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(
                RefreshRequestModel.seller_id == seller_id,
                RefreshRequestModel.status.in_(("queued", "running")),
            )
            .order_by(RefreshRequestModel.requested_at)
            .limit(1)
        )

    async def latest_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(RefreshRequestModel.seller_id == seller_id)
            .order_by(RefreshRequestModel.requested_at.desc())
            .limit(1)
        )

    async def claim_refreshes(self, limit: int = 5) -> list[RefreshRequestModel]:
        """Take waiting requests. Skips locked rows so two ticks cannot collide."""
        requests = list(
            await self.session.scalars(
                select(RefreshRequestModel)
                .where(RefreshRequestModel.status == "queued")
                .order_by(RefreshRequestModel.requested_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for request in requests:
            request.status = "running"
            request.started_at = datetime.now(UTC)
        return requests

    async def abandon_stale_refreshes(self, started_before: datetime) -> int:
        """Close requests a dead worker left `running`.

        Without this a restart mid-collection pins the seller's button to that
        request forever: the active-request index lets no new one in.
        """
        result = await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.status == "running", RefreshRequestModel.started_at < started_before)
            .values(status="error", error="Сбор прервался: воркер перезапустился", finished_at=datetime.now(UTC))
            .returning(RefreshRequestModel.id)
        )
        return len(result.all())

    async def finish_refresh(self, request_id: uuid.UUID, error: str | None = None) -> None:
        await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.id == request_id)
            .values(
                status="error" if error else "success",
                error=error[:1000] if error else None,
                finished_at=datetime.now(UTC),
            )
        )

    # --- mapping ----------------------------------------------------------

    @staticmethod
    def _card_row(seller_id: uuid.UUID, card: CardFacts, now: datetime) -> dict[str, Any]:
        return {
            "seller_id": seller_id,
            "article": card.article,
            "vendor_code": card.vendor_code,
            "title": card.title[:512],
            "barcode": card.barcode[:64],
            "imt_id": card.imt_id,
            "subject_id": card.subject_id,
            "subject_name": card.subject_name[:255],
            "photo_url": card.photo_url[:1024],
            "photo_count": card.photo_count,
            "description_length": card.description_length,
            "has_video": card.has_video,
            "characteristic_ids": sorted(card.characteristic_ids),
            "card_created_at": card.card_created_at,
            "collected_at": now,
        }

    @staticmethod
    def _card(row: CardFactsModel) -> CardFacts:
        return CardFacts(
            article=row.article,
            vendor_code=row.vendor_code,
            title=row.title,
            barcode=row.barcode,
            imt_id=row.imt_id,
            subject_id=row.subject_id,
            subject_name=row.subject_name,
            photo_url=row.photo_url,
            photo_count=row.photo_count,
            description_length=row.description_length,
            has_video=row.has_video,
            characteristic_ids=frozenset(int(value) for value in row.characteristic_ids or []),
            card_created_at=row.card_created_at,
        )
