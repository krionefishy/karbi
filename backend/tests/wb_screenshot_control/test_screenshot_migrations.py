"""Цепочка миграций `wb_screenshot_control`: вниз до base и обратно, схема совпадает с моделями.

Нужен PostgreSQL из `deploy/compose.test.yaml`. Alembic запускается отдельным процессом: его
env.py сам крутит event loop, а внутри теста loop уже занят.
"""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.modules.wb_screenshot_control.infrastructure.postgres import WBScreenshotControlBase
from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
ROOT = Path(__file__).resolve().parents[3]
SCHEMA = "wb_screenshot_control"


def alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-n", SCHEMA, *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": SETTINGS.database.url},
        check=True,
        capture_output=True,
    )


def _only_this_schema(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001 — сигнатура alembic
    if type_ == "table":
        return obj.schema == SCHEMA and name != "alembic_version"
    return True


def _differences(connection) -> list:  # noqa: ANN001 — синхронное соединение из run_sync
    context = MigrationContext.configure(
        connection,
        opts={
            "include_schemas": True,
            "include_object": _only_this_schema,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    return compare_metadata(context, WBScreenshotControlBase.metadata)


async def test_the_chain_goes_down_and_up_and_matches_the_models() -> None:
    alembic("downgrade", "base")
    engine = create_async_engine(SETTINGS.database.url)
    try:
        async with engine.connect() as connection:
            left = await connection.scalar(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = :schema"),
                {"schema": SCHEMA},
            )
        assert left == 1  # только alembic_version

        alembic("upgrade", "head")
        async with engine.connect() as connection:
            assert await connection.run_sync(_differences) == []
    finally:
        await engine.dispose()


async def test_the_database_guards_uniqueness_and_vocabularies() -> None:
    engine = create_async_engine(SETTINGS.database.url)
    sha = uuid.uuid4().hex + uuid.uuid4().hex
    file_id = uuid.uuid4()
    insert_file = text(
        f"INSERT INTO {SCHEMA}.files (id, sha256, size_bytes, mime, width, height) "
        "VALUES (:id, :sha, 10, 'image/png', 800, 600)"
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(insert_file, {"id": file_id, "sha": sha})
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(insert_file, {"id": uuid.uuid4(), "sha": sha})

        submission = text(
            f"INSERT INTO {SCHEMA}.submissions (id, user_id, channel, client_submission_id, file_id, outcome) "
            "VALUES (:id, :user, :channel, 'same-client-id', :file, :outcome)"
        )
        user = uuid.uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                submission, {"id": uuid.uuid4(), "user": user, "channel": "web", "file": file_id, "outcome": None}
            )
        for row in (
            {"user": user, "channel": "web", "outcome": "positive"},
            {"user": uuid.uuid4(), "channel": "email", "outcome": None},
            {"user": uuid.uuid4(), "channel": "web", "outcome": "maybe"},
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await connection.execute(submission, {"id": uuid.uuid4(), "file": file_id, **row})

        code = text(
            f"INSERT INTO {SCHEMA}.pairing_codes (id, user_id, code, expires_at, used_at) "
            "VALUES (:id, :user, 'K7Q2M9XA', :expires, :used)"
        )
        expires = datetime.now(UTC) + timedelta(minutes=15)
        async with engine.begin() as connection:
            await connection.execute(code, {"id": uuid.uuid4(), "user": user, "expires": expires, "used": expires})
            await connection.execute(code, {"id": uuid.uuid4(), "user": user, "expires": expires, "used": None})
        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(code, {"id": uuid.uuid4(), "user": user, "expires": expires, "used": None})
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f"DELETE FROM {SCHEMA}.pairing_codes WHERE code = 'K7Q2M9XA'"))
            await connection.execute(text(f"DELETE FROM {SCHEMA}.submissions WHERE file_id = :file"), {"file": file_id})
            await connection.execute(text(f"DELETE FROM {SCHEMA}.files WHERE id = :file"), {"file": file_id})
        await engine.dispose()
