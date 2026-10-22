"""Поиск кандидатов для решения: каждый признак находит своего кандидата, лишние не попадают."""

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest_asyncio

from backend.modules.wb_screenshot_control.application.serialization import fields_to_json
from backend.modules.wb_screenshot_control.domain.hashing import to_bits
from backend.modules.wb_screenshot_control.domain.pipeline_types import ChatMessage
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    ExtractionModel,
    FileModel,
    SubmissionModel,
)
from backend.modules.wb_screenshot_control.infrastructure.postgres.repository import (
    CandidateQuery,
    ScreenshotRepository,
)
from backend.storage.pg import Database
from backend.tests.wb_screenshot_control.fakes import BASE, desktop_fields, reset

NOW = datetime.now(UTC).replace(microsecond=0)
KEY_TIME = datetime(2026, 10, 22, 3, 39, tzinfo=UTC)
BASE_HASH = 0x0F0F_F0F0_3C3C_C3C3


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(BASE.database.url, pool_size=2, max_overflow=0)
    await reset(database)
    try:
        yield database
    finally:
        await reset(database)
        await database.disconnect()


async def add(
    database: Database,
    name: str,
    *,
    dhash: int | None = None,
    received_at: datetime = NOW - timedelta(days=1),
    verdict: str | None = "accepted",
    fields: dict | None = None,
    **keys: object,
) -> uuid.UUID:
    sha = hashlib.sha256(name.encode()).hexdigest()
    async with database.session() as session:
        file = FileModel(sha256=sha, size_bytes=10, mime="image/png", width=800, height=600)
        session.add(file)
        await session.flush()
        if dhash is not None:
            await ScreenshotRepository(session).set_dhash(file.id, to_bits(dhash))
        if fields is not None:
            session.add(ExtractionModel(file_id=file.id, pipeline_version="v1", status="ok", fields=fields))
        submission = SubmissionModel(
            user_id=uuid.uuid4(),
            channel="web",
            client_submission_id=name,
            file_id=file.id,
            received_at=received_at,
            stage="decided" if verdict else "extracted",
            verdict=verdict,
            **keys,
        )
        session.add(submission)
        await session.commit()
        return submission.id


async def found(database: Database, query: CandidateQuery) -> set[uuid.UUID]:
    async with database.session() as session:
        return {row.submission.id for row in await ScreenshotRepository(session).candidates(query)}


def query(me: uuid.UUID, file_id: uuid.UUID, **criteria: object) -> CandidateQuery:
    return CandidateQuery(
        submission_id=me,
        file_id=file_id,
        since=NOW - timedelta(days=90),
        pipeline_version="v1",
        **criteria,  # type: ignore[arg-type]
    )


async def test_each_key_finds_its_candidate_and_only_it(database: Database) -> None:
    chat = str(uuid.uuid4())
    by_chat = await add(database, "chat", chat_id_norm=chat)
    by_text = await add(database, "text", text_key="a" * 40)
    by_time = await add(database, "time", key_time=KEY_TIME + timedelta(minutes=1))
    far_time = await add(database, "far-time", key_time=KEY_TIME + timedelta(minutes=5))
    near_image = await add(database, "near", dhash=BASE_HASH ^ 0b111)
    far_image = await add(database, "far", dhash=BASE_HASH ^ ((1 << 40) - 1))
    await add(database, "rejected", chat_id_norm=chat, verdict="rejected")
    await add(database, "too-old", chat_id_norm=chat, received_at=NOW - timedelta(days=200))
    undecided = await add(database, "undecided", chat_id_norm=chat, verdict=None)

    me = await add(database, "me", chat_id_norm=chat, text_key="a" * 40, key_time=KEY_TIME, verdict=None)
    async with database.session() as session:
        mine = await session.get(SubmissionModel, me)
    assert mine is not None
    candidates = await found(
        database,
        query(
            me,
            mine.file_id,
            chat_id=chat,
            text_key="a" * 40,
            key_time=KEY_TIME,
            dhash_bits=to_bits(BASE_HASH),
            dhash_max_distance=12,
        ),
    )
    assert candidates == {by_chat, by_text, by_time, near_image, undecided}
    assert far_time not in candidates and far_image not in candidates

    day_wide = await found(
        database, query(me, mine.file_id, key_time=KEY_TIME + timedelta(hours=20), day_precision=True)
    )
    assert by_time in day_wide and far_time in day_wide


async def test_the_same_file_is_always_a_candidate(database: Database) -> None:
    first = await add(database, "shared")
    async with database.session() as session:
        original = await session.get(SubmissionModel, first)
        assert original is not None
        copy = SubmissionModel(
            user_id=uuid.uuid4(),
            channel="web",
            client_submission_id="copy",
            file_id=original.file_id,
            stage="extracted",
        )
        session.add(copy)
        await session.commit()
    assert await found(database, query(copy.id, original.file_id)) == {first}


async def test_mobile_screens_meet_by_any_visible_message(database: Database) -> None:
    def mobile(*texts: str) -> dict:
        messages = tuple(ChatMessage(text_norm=text, hour=10, minute=5, side="client") for text in texts)
        return fields_to_json(replace(desktop_fields(), layout="mobile_app", chat_id=None, messages=messages))

    shared = "когда вернете деньги за брак"
    overlap = await add(database, "phone-1", case_kind="mobile", fields=mobile("здравствуйте", shared))
    await add(database, "phone-2", case_kind="mobile", fields=mobile("совсем другой разговор"))
    await add(database, "desktop", case_kind="desktop", fields=mobile(shared))  # не телефон — не ищем

    me = await add(database, "phone-me", case_kind="mobile", verdict=None)
    async with database.session() as session:
        mine = await session.get(SubmissionModel, me)
    assert mine is not None
    assert await found(database, query(me, mine.file_id, message_texts=(shared, "ещё одна реплика"))) == {overlap}
