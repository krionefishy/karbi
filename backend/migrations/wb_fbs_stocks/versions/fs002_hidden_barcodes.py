"""Скрытые баркоды: строка выключается из таблицы и опроса, но не стирается.

Селлер скрывает то, что сейчас не нужно, и возвращает одним нажатием; заметка
и история строки при этом остаются.
"""

import sqlalchemy as sa
from alembic import op

revision = "fs002"
down_revision = "fs001"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_stocks"


def upgrade() -> None:
    op.add_column("barcodes", sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("barcodes", "hidden_at", schema=SCHEMA)
