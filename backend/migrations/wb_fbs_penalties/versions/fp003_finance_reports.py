"""Отчёты реализации как единица сбора: список отчётов и курсор детализации по каждому.

Старый метод детализации за период у токенов селлеров даёт два запроса в
сутки; новый финансовый API отдаёт список отчётов и детализацию по ID отчёта
с лимитом раз в минуту. Отчёт после формирования не меняется, поэтому
загружается один раз: курсор `rrdId` и отметка загрузки хранятся на отчёте.
Курсор догрузки на кабинете больше не нужен.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fp003"
down_revision = "fp002"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_penalties"
MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", sa.BigInteger(), primary_key=True),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("create_date", sa.Date(), nullable=True),
        sa.Column("report_type", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("penalty_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("deduction_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("cursor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_penalties_reports_period", "reports", ["seller_id", "date_from"], schema=SCHEMA)
    for column in ("backfill_done", "backfill_cursor", "backfill_to", "backfill_from"):
        op.drop_column("tracked_sellers", column, schema=SCHEMA)


def downgrade() -> None:
    op.add_column("tracked_sellers", sa.Column("backfill_from", sa.Date(), nullable=True), schema=SCHEMA)
    op.add_column("tracked_sellers", sa.Column("backfill_to", sa.Date(), nullable=True), schema=SCHEMA)
    op.add_column(
        "tracked_sellers",
        sa.Column("backfill_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "tracked_sellers",
        sa.Column("backfill_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.drop_table("reports", schema=SCHEMA)
