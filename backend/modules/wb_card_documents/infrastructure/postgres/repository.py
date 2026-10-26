import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_documents.infrastructure.postgres.models import (
    CardStateModel,
    NotificationLogModel,
    RefreshRequestModel,
    TrackedSellerModel,
    TransitionModel,
)

_CHUNK = 500
_STATE_COLUMNS = (
    "vendor_code",
    "title",
    "subject_name",
    "state",
    "reason",
    "document_number",
    "document_start",
    "document_end",
    "document_endless",
    "threshold",
    "episode_alerted",
    "verdict_at",
    "overall_status",
    "overall_reason",
    "overall_at",
    "documents",
    "declared",
    "since",
    "checked_at",
    "closed_at",
)


class CardDocumentsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def tracked_many(self, seller_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, TrackedSellerModel]:
        wanted = set(seller_ids)
        if not wanted:
            return {}
        rows = await self.session.scalars(select(TrackedSellerModel).where(TrackedSellerModel.seller_id.in_(wanted)))
        return {row.seller_id: row for row in rows}

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        for model in (CardStateModel, TransitionModel, NotificationLogModel, RefreshRequestModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def still_tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        """The enrollment at write time, row-locked so a concurrent purge waits or wins outright."""
        return await self.session.scalar(
            select(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).with_for_update(read=True)
        )

    async def sellers_due(self, since: datetime, *, retry_after: datetime) -> list[uuid.UUID]:
        """Never-collected sellers first, then those not collected since `since` and not tried since `retry_after`."""
        rows = await self.session.scalars(
            select(TrackedSellerModel.seller_id)
            .where(
                (TrackedSellerModel.collected_at.is_(None)) | (TrackedSellerModel.collected_at < since),
                (TrackedSellerModel.attempted_at.is_(None)) | (TrackedSellerModel.attempted_at < retry_after),
            )
            .order_by(TrackedSellerModel.collected_at.asc().nulls_first(), TrackedSellerModel.enrolled_at)
        )
        return list(rows)

    async def record_attempt(self, seller_id: uuid.UUID, now: datetime | None = None) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(attempted_at=now or datetime.now(UTC))
        )

    async def finish_collection(
        self,
        seller_id: uuid.UUID,
        now: datetime,
        *,
        baseline: bool,
        full: bool = False,
        cursor: tuple[datetime, int] | None = None,
    ) -> None:
        values: dict[str, Any] = {"collected_at": now, "collection_error": None, "collection_error_permanent": False}
        if baseline:
            values["baseline_at"] = now
        if full:
            values["full_collected_at"] = now
        if cursor is not None:
            values["cursor_updated_at"], values["cursor_nm_id"] = cursor
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(**values)
        )

    async def fail_collection(self, seller_id: uuid.UUID, error: str, *, permanent: bool = False) -> None:
        """Keep the previous states and their date; only say what went wrong and whether a retry can help."""
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collection_error=error[:1000], collection_error_permanent=permanent)
        )

    async def collection_summary(self, seller_ids: set[uuid.UUID]) -> tuple[datetime | None, int]:
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

    async def open_states(self, seller_id: uuid.UUID) -> dict[int, CardStateModel]:
        rows = await self.session.scalars(
            select(CardStateModel).where(CardStateModel.seller_id == seller_id, CardStateModel.closed_at.is_(None))
        )
        return {row.nm_id: row for row in rows}

    async def save_states(self, rows: Sequence[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), _CHUNK):
            statement = insert(CardStateModel).values(list(rows[offset : offset + _CHUNK]))
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "nm_id"],
                    set_={column: statement.excluded[column] for column in _STATE_COLUMNS},
                )
            )

    async def close_states(self, seller_id: uuid.UUID, nm_ids: Iterable[int], now: datetime) -> int:
        wanted = sorted(set(nm_ids))
        for offset in range(0, len(wanted), _CHUNK):
            await self.session.execute(
                update(CardStateModel)
                .where(
                    CardStateModel.seller_id == seller_id,
                    CardStateModel.nm_id.in_(wanted[offset : offset + _CHUNK]),
                    CardStateModel.closed_at.is_(None),
                )
                .values(closed_at=now)
            )
        return len(wanted)

    async def add_transitions(self, rows: Sequence[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(TransitionModel).values(list(rows[offset : offset + _CHUNK])))

    async def transitions(self, seller_id: uuid.UUID) -> list[TransitionModel]:
        rows = await self.session.scalars(
            select(TransitionModel)
            .where(TransitionModel.seller_id == seller_id)
            .order_by(TransitionModel.happened_at, TransitionModel.nm_id)
        )
        return list(rows)

    async def logged(self, seller_id: uuid.UUID, kind: str, key: str) -> bool:
        found = await self.session.scalar(
            select(NotificationLogModel.key).where(
                NotificationLogModel.seller_id == seller_id,
                NotificationLogModel.kind == kind,
                NotificationLogModel.key == key[:255],
            )
        )
        return found is not None

    async def log_sent(self, seller_id: uuid.UUID, kind: str, key: str, now: datetime) -> None:
        statement = insert(NotificationLogModel).values(seller_id=seller_id, kind=kind, key=key[:255], sent_at=now)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id", "kind", "key"]))

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequestModel:
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
