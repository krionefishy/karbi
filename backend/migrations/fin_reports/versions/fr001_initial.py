"""Финансовые отчёты: подключённые кабинеты и себестоимость артикулов.

Деньги отчёта лежат в зеркале отчётов реализации `wb_core` и считаются при
чтении; своё у модуля — только то, чего WB не знает: закупочная цена
артикула в кабинете. Версии цены не переписываются — новая действует со
своей даты, прошлые периоды остаются как были.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fr001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "fin_reports"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "cost_prices",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("marketplace", sa.String(8), primary_key=True),
        sa.Column("article", sa.String(64), primary_key=True),
        sa.Column("effective_from", sa.Date(), primary_key=True),
        sa.Column("vendor_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("marketplace IN ('wb', 'ozon')", name="ck_fin_reports_cost_prices_marketplace"),
        sa.CheckConstraint("cost >= 0", name="ck_fin_reports_cost_prices_cost"),
        schema=SCHEMA,
    )
    op.create_index("ix_fin_reports_cost_prices_seller", "cost_prices", ["seller_id", "marketplace"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("cost_prices", schema=SCHEMA)
    op.drop_table("tracked_sellers", schema=SCHEMA)
