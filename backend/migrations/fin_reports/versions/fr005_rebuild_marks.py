"""Отпечаток дня Ozon на сложенных суммах и отметка снятой недели остатков.

Зеркало Ozon перечитывает последние дни и переносит начисления между днями —
сложенный день должен складываться заново, когда его отпечаток в зеркале сменился.
Снимок остатков с нулём строк раньше не считался снятым и повторялся всю неделю.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fr005"
down_revision = "fr004"
branch_labels = None
depends_on = None

SCHEMA = "fin_reports"


def upgrade() -> None:
    op.add_column(
        "ozon_days", sa.Column("mirror_lines", sa.Integer(), nullable=False, server_default="0"), schema=SCHEMA
    )
    op.add_column(
        "ozon_days", sa.Column("mirror_collected_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.create_table(
        "wb_stock_weeks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("week_end", sa.Date(), primary_key=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    # Недели, снятые до отметок: их строки уже есть, отметка — по ним.
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.wb_stock_weeks (seller_id, week_end, collected_at, taken_at, rows)
        SELECT seller_id, week_end, min(taken_at), min(taken_at), count(*)
        FROM {SCHEMA}.wb_stock_snapshots GROUP BY seller_id, week_end
        """
    )


def downgrade() -> None:
    op.drop_table("wb_stock_weeks", schema=SCHEMA)
    op.drop_column("ozon_days", "mirror_collected_at", schema=SCHEMA)
    op.drop_column("ozon_days", "mirror_lines", schema=SCHEMA)
