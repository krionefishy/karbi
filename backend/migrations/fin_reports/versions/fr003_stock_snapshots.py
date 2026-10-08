"""Снимки остатков на конец недели для листов «По артикулам» и «Остатки».

Зеркало wb_core держит только текущий остаток. Лист просит остаток «на
последнюю неделю», поэтому воркер снимает его в начале следующей недели и
хранит у себя: это уже не ответ WB, а наш срез во времени.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fr003"
down_revision = "fr002"
branch_labels = None
depends_on = None

SCHEMA = "fin_reports"


def upgrade() -> None:
    op.create_table(
        "wb_stock_snapshots",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("week_end", sa.Date(), primary_key=True),
        sa.Column("nm_id", sa.BigInteger(), primary_key=True),
        sa.Column("tech_size", sa.String(64), primary_key=True, server_default=""),
        sa.Column("in_warehouse", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("to_client", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("from_client", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("wb_stock_snapshots", schema=SCHEMA)
