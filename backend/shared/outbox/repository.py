import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def add(self, *, aggregate_id: uuid.UUID, event_type: str, topic: str, payload: dict[str, Any]) -> uuid.UUID:
        event_id = uuid.uuid4()
        payload = {**payload, "event_id": str(event_id)}
        self.session.add(
            OutboxEventModel(
                id=event_id,
                aggregate_id=aggregate_id,
                event_type=event_type,
                topic=topic,
                message_key=str(aggregate_id),
                payload=payload,
            )
        )
        return event_id

    async def claim(self, worker_id: str, limit: int = 50) -> list[OutboxEventModel]:
        now = datetime.now(UTC)
        rows = list(
            await self.session.scalars(
                select(OutboxEventModel)
                .where(
                    OutboxEventModel.published_at.is_(None),
                    OutboxEventModel.next_attempt_at <= now,
                    (OutboxEventModel.locked_until.is_(None) | (OutboxEventModel.locked_until < now)),
                )
                .order_by(OutboxEventModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for row in rows:
            row.locked_by = worker_id
            row.locked_until = now + timedelta(seconds=30)
            row.attempts += 1
        await self.session.commit()
        return rows

    async def published(self, event_id: uuid.UUID) -> None:
        row = await self.session.get(OutboxEventModel, event_id)
        if row:
            row.published_at = datetime.now(UTC)
            row.locked_by = None
            row.locked_until = None
            row.last_error = None
            await self.session.commit()

    async def failed(self, event_id: uuid.UUID, error: str, attempts: int) -> None:
        row = await self.session.get(OutboxEventModel, event_id)
        if row:
            delay = min(900, 2 ** min(attempts, 10))
            row.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
            row.last_error = error[:1000]
            row.locked_by = None
            row.locked_until = None
            await self.session.commit()
