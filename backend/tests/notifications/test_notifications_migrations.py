"""nt005: интерактивные боты и вид строки очереди — вниз до nt004 и обратно, без потерь для старых строк."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from backend.shared.settings import load_settings

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
ROOT = Path(__file__).resolve().parents[3]
NEW_BOT_COLUMNS = {"interactive"}
NEW_QUEUE_COLUMNS = {"kind", "buttons", "reply_to", "target_ref"}


def alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-n", "notifications", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": SETTINGS.database.url},
        check=True,
        capture_output=True,
    )


async def columns(connection, table: str) -> set[str]:  # noqa: ANN001
    rows = await connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = 'notifications' AND table_name = :t"
        ),
        {"t": table},
    )
    return {row[0] for row in rows}


async def test_nt005_goes_down_and_up_and_old_rows_read_as_plain_messages() -> None:
    engine = create_async_engine(SETTINGS.database.url)
    bot_id = uuid.uuid4()
    try:
        alembic("downgrade", "nt004")
        async with engine.begin() as connection:
            assert not NEW_BOT_COLUMNS & await columns(connection, "bots")
            assert not NEW_QUEUE_COLUMNS & await columns(connection, "outgoing_messages")
            await connection.execute(
                text("INSERT INTO notifications.bots (id, code, title, invite_link_template) VALUES (:id, :c, '', '')"),
                {"id": bot_id, "c": f"mig-{bot_id.hex[:8]}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO notifications.outgoing_messages"
                    " (id, bot_id, chat_id, dedupe_key, template, params, text)"
                    " VALUES (:id, :bot, 1, :key, 'x', '{}'::jsonb, 'old')"
                ),
                {"id": uuid.uuid4(), "bot": bot_id, "key": f"mig:{bot_id}"},
            )

        alembic("upgrade", "head")
        async with engine.begin() as connection:
            assert await columns(connection, "bots") >= NEW_BOT_COLUMNS
            assert await columns(connection, "outgoing_messages") >= NEW_QUEUE_COLUMNS
            interactive = await connection.scalar(
                text("SELECT interactive FROM notifications.bots WHERE id = :id"), {"id": bot_id}
            )
            row = (
                await connection.execute(
                    text(
                        "SELECT kind, buttons, reply_to, target_ref FROM notifications.outgoing_messages"
                        " WHERE bot_id = :bot"
                    ),
                    {"bot": bot_id},
                )
            ).one()
            assert interactive is False
            assert tuple(row) == ("message", None, None, None)

        with pytest.raises(IntegrityError):
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO notifications.outgoing_messages"
                        " (id, bot_id, chat_id, dedupe_key, template, params, text, kind)"
                        " VALUES (:id, :bot, 1, :key, 'x', '{}'::jsonb, 't', 'photo')"
                    ),
                    {"id": uuid.uuid4(), "bot": bot_id, "key": f"mig2:{bot_id}"},
                )
    finally:
        alembic("upgrade", "head")
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM notifications.bots WHERE id = :id"), {"id": bot_id})
        await engine.dispose()
