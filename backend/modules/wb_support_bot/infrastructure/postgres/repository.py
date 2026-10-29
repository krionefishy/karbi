import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, delete, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.modules.wb_support_bot.infrastructure.postgres.models import (
    MODE_DRY_RUN,
    MODE_LIVE,
    SEND_DRY_RUN,
    SEND_FAILED,
    SEND_SENT,
    SEND_UNKNOWN,
    ControlModel,
    DialogModel,
    ModeChangeModel,
    MonitorEventModel,
    MonitorStateModel,
    ReplyVerdictModel,
    SendModel,
    TemplateModel,
    TrackedSellerModel,
    VerdictModel,
)

JOURNAL_STATUSES = (SEND_SENT, SEND_UNKNOWN, SEND_FAILED, SEND_DRY_RUN)


@dataclass(frozen=True, slots=True)
class DialogKey:
    seller_id: uuid.UUID
    chat_id: str
    prompt_at: datetime


class SupportBotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id, mode=MODE_DRY_RUN)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        for model in (
            SendModel,
            DialogModel,
            VerdictModel,
            ReplyVerdictModel,
            ModeChangeModel,
            MonitorStateModel,
            MonitorEventModel,
            TemplateModel,
            TrackedSellerModel,
        ):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))

    async def set_mode(self, seller_id: uuid.UUID, mode: str) -> None:
        self.session.add(ModeChangeModel(seller_id=seller_id, mode=mode))
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(mode=mode, paused_reason=None)
        )

    async def set_paused_reason(self, seller_id: uuid.UUID, reason: str | None) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(paused_reason=reason)
        )

    async def is_paused(self) -> bool:
        return bool(await self.session.scalar(select(ControlModel.paused).where(ControlModel.id == 1)))

    async def set_paused(self, paused: bool) -> None:
        statement = insert(ControlModel).values(id=1, paused=paused)
        await self.session.execute(
            statement.on_conflict_do_update(index_elements=["id"], set_={"paused": paused, "updated_at": func.now()})
        )

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

    async def dialogs_by_keys(self, seller_id: uuid.UUID, since: datetime) -> dict[tuple[str, datetime], DialogModel]:
        rows = await self.session.scalars(
            select(DialogModel).where(DialogModel.seller_id == seller_id, DialogModel.prompt_at >= since)
        )
        return {(row.chat_id, row.prompt_at): row for row in rows}

    async def retire_out_of_window(self, seller_id: uuid.UUID, before: datetime) -> int:
        """Диалоги старше окна разбора больше не пересчитываются: рассылку по ним снимаем.

        Без этого диалог, вышедший за окно посреди кампании, навсегда оставался бы
        «идёт рассылка» в сводке, хотя ничего не отправляется.
        """
        result = await self.session.execute(
            update(DialogModel)
            .where(
                DialogModel.seller_id == seller_id,
                DialogModel.prompt_at < before,
                DialogModel.state == "in_campaign",
            )
            .values(
                state=case((DialogModel.campaign_started_at.is_not(None), "exhausted"), else_="awaiting_buyer"),
                next_send_at=None,
                reply_hold=False,
                updated_at=func.now(),
            )
        )
        return int(getattr(result, "rowcount", 0) or 0)

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
                ~DialogModel.reply_hold,
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

    async def list_sends(
        self, seller_id: uuid.UUID, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[tuple[SendModel, int | None]], int]:
        """Страница журнала с артикулом диалога и общее число строк под фильтр."""
        conditions: list[ColumnElement[bool]] = [
            SendModel.seller_id == seller_id,
            SendModel.status.in_(JOURNAL_STATUSES),
        ]
        if status:
            conditions.append(SendModel.status == status)
        total = await self.session.scalar(select(func.count()).select_from(SendModel).where(*conditions))
        rows = await self.session.execute(
            select(SendModel, DialogModel.nm_id)
            .outerjoin(
                DialogModel,
                (DialogModel.seller_id == SendModel.seller_id)
                & (DialogModel.chat_id == SendModel.chat_id)
                & (DialogModel.prompt_at == SendModel.prompt_at),
            )
            .where(*conditions)
            .order_by(SendModel.planned_at.desc(), SendModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [(send, nm_id) for send, nm_id in rows], int(total or 0)

    async def send_status_counts(self, seller_id: uuid.UUID) -> dict[str, int]:
        rows = await self.session.execute(
            select(SendModel.status, func.count())
            .where(SendModel.seller_id == seller_id, SendModel.status.in_(JOURNAL_STATUSES))
            .group_by(SendModel.status)
        )
        return {status: int(count) for status, count in rows}

    async def last_activity(self, seller_id: uuid.UUID) -> datetime | None:
        return await self.session.scalar(select(func.max(SendModel.planned_at)).where(SendModel.seller_id == seller_id))

    async def verdicts_by_tail(self, seller_id: uuid.UUID, prompt_version: str) -> dict[tuple[str, str], VerdictModel]:
        """Все вердикты кабинета для версии промпта, по паре (чат, отпечаток хвоста)."""
        rows = await self.session.scalars(
            select(VerdictModel).where(
                VerdictModel.seller_id == seller_id, VerdictModel.prompt_version == prompt_version
            )
        )
        return {(row.chat_id, row.tail_fingerprint): row for row in rows}

    async def reply_verdicts_by_slice(
        self, seller_id: uuid.UUID, prompt_version: str, since: datetime
    ) -> dict[tuple[str, str], ReplyVerdictModel]:
        """Классы ответов кабинета для версии промпта, по паре (чат, отпечаток среза)."""
        rows = await self.session.scalars(
            select(ReplyVerdictModel).where(
                ReplyVerdictModel.seller_id == seller_id,
                ReplyVerdictModel.prompt_version == prompt_version,
                ReplyVerdictModel.last_event_at >= since,
            )
        )
        return {(row.chat_id, row.slice_fingerprint): row for row in rows}

    async def effect_rows(
        self, seller_id: uuid.UUID, since: datetime
    ) -> list[tuple[str, datetime, datetime, bool, datetime | None]]:
        """Диалоги с отправками: первая отправка (живая или сухая), была ли живая, первый ответ после неё."""
        first = (
            select(
                SendModel.chat_id,
                SendModel.prompt_at,
                func.min(SendModel.planned_at).label("first_at"),
                func.bool_or(SendModel.status == SEND_SENT).label("live"),
            )
            .where(
                SendModel.seller_id == seller_id,
                SendModel.status.in_((SEND_SENT, SEND_UNKNOWN, SEND_DRY_RUN)),
            )
            .group_by(SendModel.chat_id, SendModel.prompt_at)
            .subquery()
        )
        rows = await self.session.execute(
            select(first.c.chat_id, first.c.prompt_at, first.c.first_at, first.c.live, DialogModel.reply_first_at)
            .join(
                DialogModel,
                (DialogModel.seller_id == seller_id)
                & (DialogModel.chat_id == first.c.chat_id)
                & (DialogModel.prompt_at == first.c.prompt_at),
            )
            .where(first.c.first_at >= since)
        )
        return [(chat, prompt, first_at, bool(live), reply) for chat, prompt, first_at, live, reply in rows]

    async def reply_slices(self, seller_id: uuid.UUID, prompt_version: str, since: datetime) -> list[ReplyVerdictModel]:
        rows = await self.session.scalars(
            select(ReplyVerdictModel)
            .where(
                ReplyVerdictModel.seller_id == seller_id,
                ReplyVerdictModel.prompt_version == prompt_version,
                ReplyVerdictModel.last_event_at >= since,
            )
            .order_by(ReplyVerdictModel.first_event_at)
        )
        return list(rows)

    async def sent_rows(self, seller_id: uuid.UUID, since: datetime) -> list[tuple[str, datetime, datetime]]:
        rows = await self.session.execute(
            select(
                SendModel.chat_id, SendModel.prompt_at, func.coalesce(SendModel.sent_at, SendModel.planned_at)
            ).where(SendModel.seller_id == seller_id, SendModel.status == SEND_SENT, SendModel.planned_at >= since)
        )
        return [(chat, prompt, moment) for chat, prompt, moment in rows]

    async def dialog_states(
        self, seller_id: uuid.UUID, keys: set[tuple[str, datetime]]
    ) -> dict[tuple[str, datetime], DialogModel]:
        if not keys:
            return {}
        rows = await self.session.scalars(
            select(DialogModel).where(
                DialogModel.seller_id == seller_id, tuple_(DialogModel.chat_id, DialogModel.prompt_at).in_(keys)
            )
        )
        return {(row.chat_id, row.prompt_at): row for row in rows}

    async def live_since(self, seller_id: uuid.UUID) -> datetime | None:
        """С какого момента кабинет в режиме «вся рассылка»; `None` — сейчас не в нём или переход не записан."""
        last = await self.session.execute(
            select(ModeChangeModel.mode, ModeChangeModel.changed_at)
            .where(ModeChangeModel.seller_id == seller_id)
            .order_by(ModeChangeModel.changed_at.desc(), ModeChangeModel.id.desc())
            .limit(1)
        )
        row = last.first()
        return row[1] if row is not None and row[0] == MODE_LIVE else None

    async def latest_reply_verdicts(
        self, seller_id: uuid.UUID, chat_ids: list[str], prompt_version: str
    ) -> dict[str, ReplyVerdictModel]:
        if not chat_ids:
            return {}
        rows = await self.session.scalars(
            select(ReplyVerdictModel)
            .where(
                ReplyVerdictModel.seller_id == seller_id,
                ReplyVerdictModel.prompt_version == prompt_version,
                ReplyVerdictModel.chat_id.in_(chat_ids),
            )
            .order_by(ReplyVerdictModel.last_event_at)
        )
        return {row.chat_id: row for row in rows}

    async def dialog_reply_verdicts(self, key: DialogKey, prompt_version: str) -> list[ReplyVerdictModel]:
        rows = await self.session.scalars(
            select(ReplyVerdictModel)
            .where(
                ReplyVerdictModel.seller_id == key.seller_id,
                ReplyVerdictModel.chat_id == key.chat_id,
                ReplyVerdictModel.prompt_at == key.prompt_at,
                ReplyVerdictModel.prompt_version == prompt_version,
            )
            .order_by(ReplyVerdictModel.first_event_at)
        )
        return list(rows)

    async def send_status_counts_since(self, seller_id: uuid.UUID, since: datetime) -> dict[str, int]:
        rows = await self.session.execute(
            select(SendModel.status, func.count())
            .where(SendModel.seller_id == seller_id, SendModel.planned_at >= since)
            .group_by(SendModel.status)
        )
        return {status: int(count) for status, count in rows}

    async def unknown_stuck_count(self, seller_id: uuid.UUID, before: datetime) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(SendModel)
            .where(SendModel.seller_id == seller_id, SendModel.status == SEND_UNKNOWN, SendModel.planned_at < before)
        )
        return int(total or 0)

    async def verdicts_pending_stuck(self, seller_id: uuid.UUID, before: datetime) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(VerdictModel)
            .where(
                VerdictModel.seller_id == seller_id,
                VerdictModel.status != "done",
                VerdictModel.requested_at < before,
            )
        )
        return int(total or 0)

    async def monitor_state(self, seller_id: uuid.UUID) -> MonitorStateModel | None:
        return await self.session.get(MonitorStateModel, seller_id)

    async def save_monitor_state(self, seller_id: uuid.UUID, checked_at: datetime, overall: str, checks: list) -> None:
        statement = insert(MonitorStateModel).values(
            seller_id=seller_id, checked_at=checked_at, overall=overall, checks=checks
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id"],
                set_={"checked_at": checked_at, "overall": overall, "checks": checks},
            )
        )

    async def open_monitor_events(self, seller_id: uuid.UUID) -> list[MonitorEventModel]:
        rows = await self.session.scalars(
            select(MonitorEventModel).where(
                MonitorEventModel.seller_id == seller_id, MonitorEventModel.ended_at.is_(None)
            )
        )
        return list(rows)

    def add_monitor_event(self, model: MonitorEventModel) -> None:
        self.session.add(model)

    async def monitor_events_since(self, seller_id: uuid.UUID, since: datetime) -> list[MonitorEventModel]:
        rows = await self.session.scalars(
            select(MonitorEventModel)
            .where(
                MonitorEventModel.seller_id == seller_id,
                (MonitorEventModel.ended_at.is_(None)) | (MonitorEventModel.ended_at >= since),
            )
            .order_by(MonitorEventModel.started_at.desc())
        )
        return list(rows)

    async def get_reply_verdict(self, verdict_id: uuid.UUID) -> ReplyVerdictModel | None:
        return await self.session.get(ReplyVerdictModel, verdict_id)

    def add_reply_verdict(self, model: ReplyVerdictModel) -> None:
        self.session.add(model)

    async def get_verdict(self, verdict_id: uuid.UUID) -> VerdictModel | None:
        return await self.session.get(VerdictModel, verdict_id)

    def add_verdict(self, model: VerdictModel) -> None:
        self.session.add(model)

    async def set_manual_verdict(self, key: DialogKey, value: str | None) -> bool:
        """Ручная отметка менеджера для текущего хвоста диалога; `None` снимает её.

        Привязана к отпечатку хвоста: появится новое сообщение — отметка перестанет действовать.
        """
        dialog = await self.get_dialog(key)
        if dialog is None:
            return False
        if value is not None and dialog.tail_fingerprint is None:
            return False
        dialog.manual_verdict = value
        dialog.manual_fingerprint = dialog.tail_fingerprint if value is not None else None
        dialog.updated_at = func.now()
        return True
