"""Запросы ручек: списки, сводки, очереди админа, установки расширения и привязки Telegram.

Конвейер и приём живут в `repository.py`; здесь — то, что нужно HTTP. Транзакцией управляет вызывающий.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Integer,
    Select,
    and_,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtensionInstallModel,
    ExtractionModel,
    FileModel,
    PairingCodeModel,
    StorageObjectModel,
    SubmissionModel,
    TelegramLinkModel,
    VerdictEventModel,
)

QUEUE_KINDS = ("review", "duplicates", "audit", "all")


@dataclass(frozen=True, slots=True)
class Window:
    """Полуинтервал по `received_at`; `None` — без границы."""

    start: datetime | None = None
    end: datetime | None = None

    def where(self) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if self.start is not None:
            conditions.append(SubmissionModel.received_at >= self.start)
        if self.end is not None:
            conditions.append(SubmissionModel.received_at < self.end)
        return conditions


@dataclass(frozen=True, slots=True)
class Counts:
    total: int = 0
    accepted: int = 0
    positive: int = 0
    amended: int = 0
    deleted: int = 0
    duplicate: int = 0
    needs_review: int = 0
    rejected: int = 0
    awaiting_outcome: int = 0
    processing: int = 0


@dataclass(frozen=True, slots=True)
class UserCounts:
    user_id: uuid.UUID
    counts: Counts
    last_submission_at: datetime | None


@dataclass(frozen=True, slots=True)
class DayCounts:
    user_id: uuid.UUID
    day: date
    counts: Counts


def _keys(values: Sequence[Any]) -> Any:
    """Ключ курсора с явными типами: asyncpg не угадывает timestamptz и uuid у голого литерала."""
    typed: list[Any] = []
    for value in values:
        if isinstance(value, datetime):
            typed.append(literal(value, DateTime(timezone=True)))
        elif isinstance(value, uuid.UUID):
            typed.append(literal(value, UUID(as_uuid=True)))
        else:
            typed.append(literal(value, Integer()))
    return tuple_(*typed)


def status_condition(status: str) -> ColumnElement[bool]:
    """Статус менеджера: вердикт или одно из двух состояний до него."""
    if status == "awaiting_outcome":
        return and_(SubmissionModel.verdict.is_(None), SubmissionModel.outcome.is_(None))
    if status == "processing":
        return and_(SubmissionModel.verdict.is_(None), SubmissionModel.outcome.is_not(None))
    return SubmissionModel.verdict == status


def _has_reason(code: str) -> ColumnElement[bool]:
    return SubmissionModel.reasons.contains([{"code": code}])


_AUDIT_OPEN = and_(SubmissionModel.audit_requested.is_(True), SubmissionModel.audit_done_at.is_(None))
# Подозрительность для сортировки очереди: 0 — high, 1 — medium, 2 — low (аудит), 3 — прочее.
SUSPICION_RANK = case(
    (SubmissionModel.reasons.contains([{"severity": "high"}]), 0),
    (SubmissionModel.verdict.in_(("needs_review", "duplicate")), 1),
    (_AUDIT_OPEN, 2),
    else_=3,
)


def _count(condition: ColumnElement[bool] | None = None) -> Any:
    return func.count() if condition is None else func.count().filter(condition)


_ACCEPTED = SubmissionModel.verdict == "accepted"
_IN_PIPELINE = and_(
    SubmissionModel.stage != "decided",
    or_(SubmissionModel.stage != "extracted", SubmissionModel.outcome.is_not(None)),
)
"""Ещё в работе у конвейера: распознанный скрин без исхода ждёт менеджера, а не конвейер."""
_COUNT_COLUMNS = (
    _count(),
    _count(_ACCEPTED),
    _count(and_(_ACCEPTED, SubmissionModel.outcome == "positive")),
    _count(and_(_ACCEPTED, SubmissionModel.outcome == "amended")),
    _count(and_(_ACCEPTED, SubmissionModel.outcome == "deleted")),
    _count(SubmissionModel.verdict == "duplicate"),
    _count(SubmissionModel.verdict == "needs_review"),
    _count(SubmissionModel.verdict == "rejected"),
    _count(status_condition("awaiting_outcome")),
    _count(status_condition("processing")),
)


def _counts(row: Sequence[Any]) -> Counts:
    return Counts(*(int(value or 0) for value in row))


class ScreenshotQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- скрины менеджера ---

    async def own_submission(
        self, user_id: uuid.UUID, submission_id: uuid.UUID, *, for_update: bool = False
    ) -> SubmissionModel | None:
        query = (
            select(SubmissionModel)
            .where(SubmissionModel.id == submission_id, SubmissionModel.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def own_submissions(
        self,
        user_id: uuid.UUID,
        *,
        window: Window,
        status: str | None = None,
        outcome: str | None = None,
        channel: str | None = None,
        after: tuple[datetime, uuid.UUID] | None = None,
        limit: int,
    ) -> list[SubmissionModel]:
        """Свежие первыми; `after` — последняя строка прошлой страницы."""
        query = select(SubmissionModel).where(SubmissionModel.user_id == user_id, *window.where())
        if status is not None:
            query = query.where(status_condition(status))
        if outcome is not None:
            query = query.where(SubmissionModel.outcome == outcome)
        if channel is not None:
            query = query.where(SubmissionModel.channel == channel)
        if after is not None:
            query = query.where(tuple_(SubmissionModel.received_at, SubmissionModel.id) < _keys(after))
        query = query.order_by(SubmissionModel.received_at.desc(), SubmissionModel.id.desc()).limit(limit)
        return list(await self.session.scalars(query))

    async def submissions(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SubmissionModel]:
        wanted = set(ids)
        if not wanted:
            return {}
        rows = await self.session.scalars(select(SubmissionModel).where(SubmissionModel.id.in_(wanted)))
        return {row.id: row for row in rows}

    async def extractions(
        self, file_ids: Iterable[uuid.UUID], pipeline_version: str
    ) -> dict[uuid.UUID, ExtractionModel]:
        wanted = set(file_ids)
        if not wanted:
            return {}
        rows = await self.session.scalars(
            select(ExtractionModel).where(
                ExtractionModel.file_id.in_(wanted), ExtractionModel.pipeline_version == pipeline_version
            )
        )
        return {row.file_id: row for row in rows}

    async def objects(self, file_ids: Iterable[uuid.UUID]) -> dict[tuple[uuid.UUID, str], StorageObjectModel]:
        wanted = set(file_ids)
        if not wanted:
            return {}
        rows = await self.session.scalars(select(StorageObjectModel).where(StorageObjectModel.file_id.in_(wanted)))
        return {(row.file_id, row.kind): row for row in rows}

    async def file_objects(self, file_id: uuid.UUID, *, for_update: bool = False) -> list[StorageObjectModel]:
        query = (
            select(StorageObjectModel)
            .where(StorageObjectModel.file_id == file_id)
            .order_by(StorageObjectModel.kind)
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return list(await self.session.scalars(query))

    async def file(self, file_id: uuid.UUID) -> FileModel | None:
        return await self.session.get(FileModel, file_id)

    async def file_submissions(self, file_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(SubmissionModel).where(SubmissionModel.file_id == file_id)
            )
            or 0
        )

    # --- KPI ---

    async def counts(self, window: Window, user_id: uuid.UUID | None = None) -> Counts:
        query = select(*_COUNT_COLUMNS).select_from(SubmissionModel).where(*window.where())
        if user_id is not None:
            query = query.where(SubmissionModel.user_id == user_id)
        row = (await self.session.execute(query)).one()
        return _counts(row)

    async def counts_by_user(self, window: Window) -> list[UserCounts]:
        query = (
            select(SubmissionModel.user_id, func.max(SubmissionModel.received_at), *_COUNT_COLUMNS)
            .where(*window.where())
            .group_by(SubmissionModel.user_id)
        )
        return [
            UserCounts(user_id=row[0], last_submission_at=row[1], counts=_counts(row[2:]))
            for row in (await self.session.execute(query)).all()
        ]

    async def counts_by_user_and_day(self, window: Window, timezone: str) -> list[DayCounts]:
        day = func.date(func.timezone(timezone, SubmissionModel.received_at))
        query = (
            select(SubmissionModel.user_id, day, *_COUNT_COLUMNS)
            .where(*window.where())
            .group_by(SubmissionModel.user_id, day)
            .order_by(day, SubmissionModel.user_id)
        )
        return [
            DayCounts(user_id=row[0], day=row[1], counts=_counts(row[2:]))
            for row in (await self.session.execute(query)).all()
        ]

    async def users_with_reason(self, window: Window, code: str) -> set[uuid.UUID]:
        rows = await self.session.scalars(
            select(SubmissionModel.user_id).where(*window.where(), _has_reason(code)).distinct()
        )
        return set(rows)

    # --- очередь админа ---

    @staticmethod
    def _queue_filter(
        query: Select[Any], *, window: Window, user_id: uuid.UUID | None, reason: str | None
    ) -> Select[Any]:
        query = query.where(*window.where())
        if user_id is not None:
            query = query.where(SubmissionModel.user_id == user_id)
        if reason is not None:
            query = query.where(_has_reason(reason))
        return query

    async def queue(
        self,
        kind: str,
        *,
        window: Window,
        user_id: uuid.UUID | None,
        reason: str | None,
        after: tuple[Any, ...] | None,
        limit: int,
    ) -> list[tuple[SubmissionModel, int]]:
        """Очереди — по подозрительности, затем старые первыми; «всё» — свежие первыми."""
        rank = SUSPICION_RANK.label("rank")
        query = self._queue_filter(select(SubmissionModel, rank), window=window, user_id=user_id, reason=reason)
        if kind == "review":
            query = query.where(SubmissionModel.verdict == "needs_review")
        elif kind == "duplicates":
            query = query.where(SubmissionModel.verdict == "duplicate")
        elif kind == "audit":
            query = query.where(_AUDIT_OPEN)
        if kind == "all":
            if after is not None:
                query = query.where(tuple_(SubmissionModel.received_at, SubmissionModel.id) < _keys(after))
            query = query.order_by(SubmissionModel.received_at.desc(), SubmissionModel.id.desc())
        else:
            if after is not None:
                query = query.where(
                    tuple_(SUSPICION_RANK, SubmissionModel.received_at, SubmissionModel.id) > _keys(after)
                )
            query = query.order_by(SUSPICION_RANK, SubmissionModel.received_at, SubmissionModel.id)
        rows = (await self.session.execute(query.limit(limit))).all()
        return [(row[0], int(row[1])) for row in rows]

    async def queue_counts(self, *, window: Window, user_id: uuid.UUID | None) -> dict[str, int]:
        query = self._queue_filter(
            select(
                _count(SubmissionModel.verdict == "needs_review"),
                _count(SubmissionModel.verdict == "duplicate"),
                _count(_AUDIT_OPEN),
            ).select_from(SubmissionModel),
            window=window,
            user_id=user_id,
            reason=None,
        )
        review, duplicates, audit = (await self.session.execute(query)).one()
        return {"review": int(review or 0), "duplicates": int(duplicates or 0), "audit": int(audit or 0)}

    async def submission(self, submission_id: uuid.UUID, *, for_update: bool = False) -> SubmissionModel | None:
        query = (
            select(SubmissionModel).where(SubmissionModel.id == submission_id).execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def events(self, submission_id: uuid.UUID) -> list[VerdictEventModel]:
        return list(
            await self.session.scalars(
                select(VerdictEventModel)
                .where(VerdictEventModel.submission_id == submission_id)
                .order_by(VerdictEventModel.at, VerdictEventModel.id)
            )
        )

    async def accepted_with_same_key(self, submission: SubmissionModel) -> uuid.UUID | None:
        """Другой принятый скрин того же кейса: тот же `text_key` (минута и текст) и тот же чат."""
        if not submission.text_key:
            return None
        return await self.session.scalar(
            select(SubmissionModel.id)
            .where(
                SubmissionModel.id != submission.id,
                SubmissionModel.verdict == "accepted",
                SubmissionModel.text_key == submission.text_key,
                SubmissionModel.chat_id_norm.is_not_distinct_from(submission.chat_id_norm),
            )
            .limit(1)
        )

    # --- конвейер ---

    async def stage_counts(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(SubmissionModel.stage, func.count()).where(_IN_PIPELINE).group_by(SubmissionModel.stage)
        )
        return {stage: int(count) for stage, count in rows.all()}

    async def oldest_undecided(self) -> datetime | None:
        return await self.session.scalar(select(func.min(SubmissionModel.received_at)).where(_IN_PIPELINE))

    async def decision_latency(self, since: datetime) -> tuple[float | None, float | None]:
        # Ожидание исхода — время менеджера, не конвейера: отсчёт от выбора исхода, если он был позже приёма.
        start = case(
            (
                SubmissionModel.outcome_set_at < SubmissionModel.decided_at,
                func.greatest(SubmissionModel.received_at, SubmissionModel.outcome_set_at),
            ),
            else_=SubmissionModel.received_at,
        )
        seconds = func.extract("epoch", SubmissionModel.decided_at - start)
        row = (
            await self.session.execute(
                select(
                    func.percentile_cont(0.5).within_group(seconds),
                    func.percentile_cont(0.95).within_group(seconds),
                ).where(SubmissionModel.decided_at >= since, SubmissionModel.decided_at.is_not(None))
            )
        ).one()
        return (float(row[0]) if row[0] is not None else None, float(row[1]) if row[1] is not None else None)

    async def extraction_stats(self, since: datetime) -> tuple[int, int, int]:
        """Распознаваний с `since`: всего, неудачных и токенов."""
        row = (
            await self.session.execute(
                select(
                    func.count(),
                    _count(ExtractionModel.status == "failed"),
                    func.coalesce(
                        func.sum(
                            func.coalesce(ExtractionModel.tokens_in, 0) + func.coalesce(ExtractionModel.tokens_out, 0)
                        ),
                        0,
                    ),
                ).where(ExtractionModel.created_at >= since)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    async def channel_counts(self, since: datetime) -> dict[str, int]:
        rows = await self.session.execute(
            select(SubmissionModel.channel, func.count())
            .where(SubmissionModel.received_at >= since)
            .group_by(SubmissionModel.channel)
        )
        return {channel: int(count) for channel, count in rows.all()}

    async def storage_stats(self) -> tuple[dict[str, int], int, datetime | None]:
        rows = await self.session.execute(
            select(StorageObjectModel.state, func.count()).group_by(StorageObjectModel.state)
        )
        states = {state: int(count) for state, count in rows.all()}
        stored_bytes = await self.session.scalar(
            select(func.coalesce(func.sum(StorageObjectModel.size_bytes), 0)).where(
                StorageObjectModel.state == "stored"
            )
        )
        oldest = await self.session.scalar(
            select(func.min(StorageObjectModel.updated_at)).where(StorageObjectModel.state.in_(("staged", "uploading")))
        )
        return states, int(stored_bytes or 0), oldest


class ExtensionStore:
    """Коды сопряжения и установки расширения. Токен хранится только хешем."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def drop_codes(self, user_id: uuid.UUID, now: datetime) -> None:
        """Живой код у сотрудника один: прежние неиспользованные гасятся, заодно чистятся просроченные."""
        await self.session.execute(
            delete(PairingCodeModel).where(
                PairingCodeModel.used_at.is_(None),
                or_(PairingCodeModel.user_id == user_id, PairingCodeModel.expires_at < now),
            )
        )

    async def add_code(self, user_id: uuid.UUID, code: str, expires_at: datetime) -> PairingCodeModel:
        model = PairingCodeModel(user_id=user_id, code=code, expires_at=expires_at)
        self.session.add(model)
        await self.session.flush()
        return model

    async def consume_code(self, code: str, now: datetime) -> PairingCodeModel | None:
        """Код гасится тем же запросом, которым находится: два обмена одного кода не пройдут."""
        code_id = await self.session.scalar(
            update(PairingCodeModel)
            .where(
                PairingCodeModel.code == code,
                PairingCodeModel.used_at.is_(None),
                PairingCodeModel.expires_at > now,
            )
            .values(used_at=now)
            .returning(PairingCodeModel.id)
        )
        if code_id is None:
            return None
        return await self.session.get(PairingCodeModel, code_id)

    async def live_installs(self, user_id: uuid.UUID) -> list[ExtensionInstallModel]:
        return list(
            await self.session.scalars(
                select(ExtensionInstallModel)
                .where(ExtensionInstallModel.user_id == user_id, ExtensionInstallModel.revoked_at.is_(None))
                .order_by(ExtensionInstallModel.created_at.desc())
            )
        )

    async def install_by_token(self, token_hash: str) -> ExtensionInstallModel | None:
        return await self.session.scalar(
            select(ExtensionInstallModel).where(ExtensionInstallModel.token_hash == token_hash)
        )

    async def revoke_same_browser(self, user_id: uuid.UUID, install_id: str, now: datetime) -> None:
        await self.session.execute(
            update(ExtensionInstallModel)
            .where(
                ExtensionInstallModel.user_id == user_id,
                ExtensionInstallModel.install_id == install_id,
                ExtensionInstallModel.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def add_install(self, **values: Any) -> ExtensionInstallModel:
        model = ExtensionInstallModel(**values)
        self.session.add(model)
        await self.session.flush()
        return model

    async def revoke(self, user_id: uuid.UUID, install_id: uuid.UUID, now: datetime) -> bool:
        """Только своя установка: чужой id ничем не отличается от несуществующего."""
        revoked = await self.session.scalar(
            update(ExtensionInstallModel)
            .where(
                ExtensionInstallModel.id == install_id,
                ExtensionInstallModel.user_id == user_id,
                ExtensionInstallModel.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(ExtensionInstallModel.id)
        )
        return revoked is not None


class TelegramLinks:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def live(self, user_id: uuid.UUID | None = None) -> list[TelegramLinkModel]:
        query = select(TelegramLinkModel).where(TelegramLinkModel.revoked_at.is_(None))
        if user_id is not None:
            query = query.where(TelegramLinkModel.user_id == user_id)
        return list(await self.session.scalars(query.order_by(TelegramLinkModel.linked_at.desc())))

    async def revoke(
        self, now: datetime, *, by: uuid.UUID, user_id: uuid.UUID | None = None, link_id: uuid.UUID | None = None
    ) -> bool:
        query = update(TelegramLinkModel).where(TelegramLinkModel.revoked_at.is_(None))
        if user_id is not None:
            query = query.where(TelegramLinkModel.user_id == user_id)
        if link_id is not None:
            query = query.where(TelegramLinkModel.id == link_id)
        result = await self.session.execute(query.values(revoked_at=now, revoked_by=by).returning(TelegramLinkModel.id))
        return bool(result.all())
