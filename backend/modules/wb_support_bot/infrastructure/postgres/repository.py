import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.modules.wb_support_bot.infrastructure.postgres.models import (
    MODE_DRY_RUN,
    SEND_DRY_RUN,
    SEND_FAILED,
    SEND_SENT,
    SEND_UNKNOWN,
    ControlModel,
    DialogModel,
    SendModel,
    TemplateModel,
    TrackedSellerModel,
)


@dataclass(frozen=True, slots=True)
class DialogKey:
    seller_id: uuid.UUID
    chat_id: str
    prompt_at: datetime


class SupportBotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- подключения и режим --------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id, mode=MODE_DRY_RUN)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge(self, seller_id: uuid.UUID) -> None:
        for model in (SendModel, DialogModel, TemplateModel, TrackedSellerModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))

    async def set_mode(self, seller_id: uuid.UUID, mode: str) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(mode=mode, paused_reason=None)
        )

    async def set_paused_reason(self, seller_id: uuid.UUID, reason: str | None) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(paused_reason=reason)
        )

    # --- общий выключатель ----------------------------------------------------

    async def is_paused(self) -> bool:
        return bool(await self.session.scalar(select(ControlModel.paused).where(ControlModel.id == 1)))

    async def set_paused(self, paused: bool) -> None:
        statement = insert(ControlModel).values(id=1, paused=paused)
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_={"paused": paused, "updated_at": func.now()})
        )

    # --- тексты ---------------------------------------------------------------

    async def templates(self, seller_id: uuid.UUID, *, active_only: bool = False) -> list[TemplateModel]:
        query = select(TemplateModel).where(TemplateModel.seller_id == seller_id).order_by(TemplateModel.id)
        if active_only:
            query = query.where(TemplateModel.active)
        return list(await self.session.scalars(query))

    async def add_template(self, seller_id: uuid.UUID, text: str) -> TemplateModel:
        model = TemplateModel(seller_id=seller_id, text=text, active=True)
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_template(self, seller_id: uuid.UUID, template_id: int) -> TemplateModel | None:
        return await self.session.scalar(
            select(TemplateModel).where(TemplateModel.seller_id == seller_id, TemplateModel.id == template_id)
        )

    async def delete_template(self, seller_id: uuid.UUID, template_id: int) -> bool:
        result = await self.session.execute(
            delete(TemplateModel).where(TemplateModel.seller_id == seller_id, TemplateModel.id == template_id)
        )
        return bool(getattr(result, "rowcount", 0))

    # --- диалоги --------------------------------------------------------------

    async def dialogs_by_keys(self, seller_id: uuid.UUID, since: datetime) -> dict[tuple[str, datetime], DialogModel]:
        rows = await self.session.scalars(
            select(DialogModel).where(DialogModel.seller_id == seller_id, DialogModel.prompt_at >= since)
        )
        return {(row.chat_id, row.prompt_at): row for row in rows}

    async def get_dialog(self, key: DialogKey) -> DialogModel | None:
        return await self.session.get(DialogModel, (key.seller_id, key.chat_id, key.prompt_at))

    async def add_dialog(self, model: DialogModel) -> None:
        self.session.add(model)

    async def due_dialogs(self, seller_id: uuid.UUID, *, now: datetime, state: str, limit: int) -> list[DialogModel]:
        """Диалоги, которым пора отправлять; строки блокируются, чтобы второй проход их не взял."""
        rows = await self.session.scalars(
            select(DialogModel)
            .where(
                DialogModel.seller_id == seller_id,
                DialogModel.state == state,
                DialogModel.next_send_at.is_not(None),
                DialogModel.next_send_at <= now,
                ~DialogModel.excluded,
                ~DialogModel.closed,
                ~DialogModel.no_contact,
            )
            .order_by(DialogModel.next_send_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(rows)

    async def list_dialogs(
        self,
        seller_id: uuid.UUID,
        *,
        state: str | None = None,
        flag: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DialogModel], int]:
        conditions: list[ColumnElement[bool]] = [DialogModel.seller_id == seller_id]
        if state:
            conditions.append(DialogModel.state == state)
        if flag == "no_contact":
            conditions.append(DialogModel.no_contact.is_(True))
        elif flag == "attention":
            conditions.append(DialogModel.attention.is_(True))
        elif flag == "excluded":
            conditions.append(DialogModel.excluded.is_(True))
        total = await self.session.scalar(select(func.count()).select_from(DialogModel).where(*conditions))
        rows = await self.session.scalars(
            select(DialogModel)
            .where(*conditions)
            .order_by(DialogModel.updated_at.desc(), DialogModel.chat_id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def state_counts(self, seller_id: uuid.UUID) -> dict[str, int]:
        rows = await self.session.execute(
            select(DialogModel.state, func.count())
            .where(DialogModel.seller_id == seller_id)
            .group_by(DialogModel.state)
        )
        return {state: int(count) for state, count in rows}

    async def set_dialog_flags(
        self, key: DialogKey, *, excluded: bool | None = None, closed: bool | None = None
    ) -> bool:
        values: dict[str, bool] = {}
        if excluded is not None:
            values["excluded"] = excluded
        if closed is not None:
            values["closed"] = closed
        if not values:
            return False
        result = await self.session.execute(
            update(DialogModel)
            .where(
                DialogModel.seller_id == key.seller_id,
                DialogModel.chat_id == key.chat_id,
                DialogModel.prompt_at == key.prompt_at,
            )
            .values(**values, updated_at=func.now())
        )
        return bool(getattr(result, "rowcount", 0))

    # --- журнал отправок ------------------------------------------------------

    async def dialog_sends(self, key: DialogKey) -> list[SendModel]:
        rows = await self.session.scalars(
            select(SendModel)
            .where(
                SendModel.seller_id == key.seller_id,
                SendModel.chat_id == key.chat_id,
                SendModel.prompt_at == key.prompt_at,
            )
            .order_by(SendModel.seq)
        )
        return list(rows)

    async def sends_by_dialog(
        self, seller_id: uuid.UUID, since: datetime
    ) -> dict[tuple[str, datetime], list[SendModel]]:
        rows = await self.session.scalars(
            select(SendModel)
            .where(SendModel.seller_id == seller_id, SendModel.prompt_at >= since)
            .order_by(SendModel.seq)
        )
        grouped: dict[tuple[str, datetime], list[SendModel]] = {}
        for row in rows:
            grouped.setdefault((row.chat_id, row.prompt_at), []).append(row)
        return grouped

    async def add_send(self, model: SendModel) -> SendModel:
        self.session.add(model)
        await self.session.flush()
        return model

    async def sent_today(self, seller_id: uuid.UUID, since: datetime) -> int:
        """Сколько сообщений кабинет уже отправил с начала суток (включая «неизвестно»)."""
        total = await self.session.scalar(
            select(func.count())
            .select_from(SendModel)
            .where(
                SendModel.seller_id == seller_id,
                SendModel.status.in_((SEND_SENT, SEND_UNKNOWN)),
                SendModel.planned_at >= since,
            )
        )
        return int(total or 0)

    async def unresolved_sends(self, seller_id: uuid.UUID) -> list[SendModel]:
        rows = await self.session.scalars(
            select(SendModel).where(SendModel.seller_id == seller_id, SendModel.status == SEND_UNKNOWN)
        )
        return list(rows)

    async def list_sends(self, seller_id: uuid.UUID, *, limit: int = 100, offset: int = 0) -> list[SendModel]:
        rows = await self.session.scalars(
            select(SendModel)
            .where(
                SendModel.seller_id == seller_id,
                SendModel.status.in_((SEND_SENT, SEND_UNKNOWN, SEND_FAILED, SEND_DRY_RUN)),
            )
            .order_by(SendModel.planned_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows)

    async def last_activity(self, seller_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(select(func.max(SendModel.planned_at)).where(SendModel.seller_id == seller_id))
