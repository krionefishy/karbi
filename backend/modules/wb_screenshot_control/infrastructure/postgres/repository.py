import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, String, and_, cast, column, exists, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import BIT, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres.models import InboxEventModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    FileModel,
    StorageObjectModel,
    SubmissionModel,
    VerdictEventModel,
)

CANDIDATE_LIMIT = 500
KEY_TIME_SLACK = timedelta(minutes=2)
DAY_SLACK = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class CandidateQuery:
    """По чему искать похожие скрины: любое совпадение даёт кандидата, решает уже `Decider`."""

    submission_id: uuid.UUID
    file_id: uuid.UUID
    since: datetime
    pipeline_version: str
    chat_id: str | None = None
    text_key: str | None = None
    key_time: datetime | None = None
    day_precision: bool = False
    dhash_bits: str | None = None
    dhash_max_distance: int = 0
    message_texts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateRow:
    submission: SubmissionModel
    sha256: str
    dhash_bits: str | None
    extraction_fields: dict[str, Any] | None


class ScreenshotRepository:
    """Весь SQL модуля для приёма, конвейера и хранилища. Транзакцией управляет вызывающий."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # inbox общий с остальными модулями: `wb_core.inbox_events`
    async def inbox_processed(self, event_id: uuid.UUID) -> bool:
        return await self.session.get(InboxEventModel, event_id) is not None

    def mark_inbox(self, event_id: uuid.UUID, event_type: str) -> None:
        self.session.add(InboxEventModel(event_id=event_id, event_type=event_type))

    # приём
    async def submission_by_client_id(
        self, user_id: uuid.UUID, client_submission_id: str
    ) -> tuple[SubmissionModel, str] | None:
        row = (
            await self.session.execute(
                select(SubmissionModel, FileModel.sha256)
                .join(FileModel, FileModel.id == SubmissionModel.file_id)
                .where(
                    SubmissionModel.user_id == user_id,
                    SubmissionModel.client_submission_id == client_submission_id,
                )
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def insert_file(self, values: dict[str, Any]) -> uuid.UUID | None:
        """`None` — файл с этим sha256 уже есть (в том числе вставлен параллельной транзакцией)."""
        return await self.session.scalar(
            insert(FileModel).values(**values).on_conflict_do_nothing(index_elements=["sha256"]).returning(FileModel.id)
        )

    async def file_by_sha(self, sha256: str) -> FileModel | None:
        return await self.session.scalar(select(FileModel).where(FileModel.sha256 == sha256))

    async def file(self, file_id: uuid.UUID) -> FileModel | None:
        return await self.session.get(FileModel, file_id)

    async def file_dhash(self, file_id: uuid.UUID) -> str | None:
        """`bit(64)` строкой из нулей и единиц: так его понимает `domain.hashing`."""
        return await self.session.scalar(select(cast(FileModel.dhash, String)).where(FileModel.id == file_id))

    async def set_dhash(self, file_id: uuid.UUID, bits: str) -> None:
        await self.session.execute(
            update(FileModel).where(FileModel.id == file_id).values(dhash=cast(literal(bits, String), BIT(64)))
        )

    async def insert_submission(self, values: dict[str, Any]) -> SubmissionModel | None:
        inserted = await self.session.scalar(
            insert(SubmissionModel)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["user_id", "client_submission_id"])
            .returning(SubmissionModel.id)
        )
        if inserted is None:
            return None
        return await self.submission(inserted)

    async def exact_original(self, file_id: uuid.UUID, exclude: uuid.UUID | None = None) -> SubmissionModel | None:
        """Самый ранний не отклонённый скрин этого же файла; ещё не решённый тоже считается."""
        query = (
            select(SubmissionModel)
            .where(SubmissionModel.file_id == file_id, SubmissionModel.verdict.is_distinct_from("rejected"))
            .order_by(SubmissionModel.received_at, SubmissionModel.id)
            .limit(1)
        )
        if exclude is not None:
            query = query.where(SubmissionModel.id != exclude)
        return await self.session.scalar(query)

    # сабмишены
    async def submission(self, submission_id: uuid.UUID, *, for_update: bool = False) -> SubmissionModel | None:
        query = (
            select(SubmissionModel).where(SubmissionModel.id == submission_id).execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    def add_verdict_event(
        self,
        submission_id: uuid.UUID,
        *,
        actor: str,
        from_verdict: str | None,
        to_verdict: str | None,
        reasons: list[dict[str, Any]],
        note: str | None = None,
    ) -> None:
        self.session.add(
            VerdictEventModel(
                submission_id=submission_id,
                actor=actor,
                from_verdict=from_verdict,
                to_verdict=to_verdict,
                reasons=reasons,
                note=note,
            )
        )

    async def earlier_undecided(self, submission: SubmissionModel, since: datetime) -> bool:
        """Есть ли более ранний по (received_at, id) и ещё не решённый скрин моложе `since`."""
        return bool(
            await self.session.scalar(
                select(
                    exists().where(
                        SubmissionModel.stage != "decided",
                        SubmissionModel.received_at >= since,
                        tuple_(SubmissionModel.received_at, SubmissionModel.id)
                        < tuple_(literal(submission.received_at), literal(submission.id)),
                    )
                )
            )
        )

    async def uploads_in_hour(self, user_id: uuid.UUID, until: datetime) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(SubmissionModel)
                .where(
                    SubmissionModel.user_id == user_id,
                    SubmissionModel.received_at > until - timedelta(hours=1),
                    SubmissionModel.received_at <= until,
                )
            )
            or 0
        )

    async def expired_without_outcome(self, cutoff: datetime, limit: int) -> Sequence[SubmissionModel]:
        return (
            await self.session.scalars(
                select(SubmissionModel)
                .where(
                    SubmissionModel.outcome.is_(None),
                    SubmissionModel.received_at < cutoff,
                    SubmissionModel.verdict.is_distinct_from("rejected"),
                )
                .order_by(SubmissionModel.received_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()

    # распознавание
    async def extraction(self, file_id: uuid.UUID, pipeline_version: str) -> ExtractionModel | None:
        return await self.session.scalar(
            select(ExtractionModel)
            .where(ExtractionModel.file_id == file_id, ExtractionModel.pipeline_version == pipeline_version)
            .execution_options(populate_existing=True)
        )

    async def upsert_extraction(self, values: dict[str, Any], *, keep_ok: bool) -> uuid.UUID:
        """Одно распознавание на файл и версию. `keep_ok`: неудача не затирает уже удачное."""
        statement = insert(ExtractionModel).values(id=uuid.uuid4(), **values)
        updated: dict[str, Any] = {
            key: statement.excluded[key] for key in values if key not in ("file_id", "pipeline_version")
        }
        updated["created_at"] = func.now()
        where = ExtractionModel.status != "ok" if keep_ok else None
        extraction_id = await self.session.scalar(
            statement.on_conflict_do_update(
                index_elements=["file_id", "pipeline_version"], set_=updated, where=where
            ).returning(ExtractionModel.id)
        )
        if extraction_id is None:
            existing = await self.extraction(values["file_id"], values["pipeline_version"])
            assert existing is not None
            return existing.id
        return extraction_id

    async def candidates(self, query: CandidateQuery) -> list[CandidateRow]:
        conditions: list[Any] = [SubmissionModel.file_id == query.file_id]
        if query.chat_id:
            conditions.append(SubmissionModel.chat_id_norm == query.chat_id)
        if query.text_key:
            conditions.append(SubmissionModel.text_key == query.text_key)
        if query.key_time is not None:
            slack = DAY_SLACK if query.day_precision else KEY_TIME_SLACK
            conditions.append(SubmissionModel.key_time.between(query.key_time - slack, query.key_time + slack))
        if query.dhash_bits is not None:
            distance = func.bit_count(FileModel.dhash.op("#")(cast(literal(query.dhash_bits), BIT(64))))
            conditions.append(and_(FileModel.dhash.is_not(None), distance <= query.dhash_max_distance))
        if query.message_texts:
            message = func.jsonb_array_elements(ExtractionModel.fields["messages"]).table_valued(column("value", JSONB))
            conditions.append(
                and_(
                    SubmissionModel.case_kind == "mobile",
                    exists(
                        select(1)
                        .select_from(message)
                        .where(message.c.value["text_norm"].astext.in_(query.message_texts))
                    ),
                )
            )
        statement: Select[Any] = (
            select(SubmissionModel, FileModel.sha256, cast(FileModel.dhash, String), ExtractionModel.fields)
            .join(FileModel, FileModel.id == SubmissionModel.file_id)
            .outerjoin(
                ExtractionModel,
                and_(
                    ExtractionModel.file_id == SubmissionModel.file_id,
                    ExtractionModel.pipeline_version == query.pipeline_version,
                    ExtractionModel.status == "ok",
                ),
            )
            .where(
                SubmissionModel.id != query.submission_id,
                SubmissionModel.verdict.is_distinct_from("rejected"),
                SubmissionModel.received_at >= query.since,
                or_(*conditions),
            )
            .order_by(SubmissionModel.received_at, SubmissionModel.id)
            .limit(CANDIDATE_LIMIT)
        )
        rows = (await self.session.execute(statement)).all()
        return [CandidateRow(row[0], row[1], row[2], row[3]) for row in rows]

    # хранилище
    async def storage_object(
        self, file_id: uuid.UUID, kind: str, *, for_update: bool = False
    ) -> StorageObjectModel | None:
        query = (
            select(StorageObjectModel)
            .where(StorageObjectModel.file_id == file_id, StorageObjectModel.kind == kind)
            .execution_options(populate_existing=True)
        )
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def insert_storage_object(self, values: dict[str, Any]) -> bool:
        """`False` — объект этого файла и вида уже заведён."""
        inserted = await self.session.scalar(
            insert(StorageObjectModel)
            .values(id=uuid.uuid4(), **values)
            .on_conflict_do_nothing(index_elements=["file_id", "kind"])
            .returning(StorageObjectModel.id)
        )
        return inserted is not None

    async def stale_objects(
        self, states: tuple[str, ...], before: datetime, limit: int
    ) -> Sequence[StorageObjectModel]:
        return (
            await self.session.scalars(
                select(StorageObjectModel)
                .where(StorageObjectModel.state.in_(states), StorageObjectModel.updated_at < before)
                .order_by(StorageObjectModel.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def live_staging_paths(self) -> set[str]:
        """Пути staging, за которыми ещё стоит невыгруженный объект."""
        rows = await self.session.scalars(
            select(StorageObjectModel.staging_path).where(
                StorageObjectModel.staging_path.is_not(None),
                StorageObjectModel.state.in_(("staged", "uploading", "failed")),
            )
        )
        return {path for path in rows if path}

    async def oldest_staged_at(self) -> datetime | None:
        return await self.session.scalar(
            select(func.min(StorageObjectModel.updated_at)).where(StorageObjectModel.state.in_(("staged", "uploading")))
        )

    async def expired_objects(
        self, kind: str, cutoff: datetime, limit: int, *, keep_unresolved: bool
    ) -> Sequence[StorageObjectModel]:
        """Выгруженные объекты, у файла которых последний скрин старше `cutoff`, а файл не закреплён.

        `keep_unresolved`: оригинал держим, пока по файлу есть скрин без вердикта или на проверке.
        """
        last_received = (
            select(func.max(SubmissionModel.received_at))
            .where(SubmissionModel.file_id == StorageObjectModel.file_id)
            .scalar_subquery()
        )
        query = (
            select(StorageObjectModel)
            .join(FileModel, FileModel.id == StorageObjectModel.file_id)
            .where(
                StorageObjectModel.kind == kind,
                StorageObjectModel.state == "stored",
                FileModel.pinned_at.is_(None),
                last_received < cutoff,
            )
            .order_by(StorageObjectModel.stored_at)
            .limit(limit)
            .with_for_update(of=StorageObjectModel, skip_locked=True)
        )
        if keep_unresolved:
            unresolved = exists().where(
                SubmissionModel.file_id == StorageObjectModel.file_id,
                or_(SubmissionModel.verdict.is_(None), SubmissionModel.verdict == "needs_review"),
            )
            query = query.where(~unresolved)
        return (await self.session.scalars(query)).all()

    async def stored_to_verify(self, limit: int) -> Sequence[StorageObjectModel]:
        """Выгруженные объекты по кругу: давно не проверенные первыми."""
        return (
            await self.session.scalars(
                select(StorageObjectModel)
                .where(StorageObjectModel.state == "stored")
                .order_by(StorageObjectModel.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def touch_objects(self, ids: Sequence[uuid.UUID]) -> None:
        if ids:
            await self.session.execute(
                update(StorageObjectModel).where(StorageObjectModel.id.in_(ids)).values(updated_at=func.now())
            )
