"""Сложенные начисления Ozon по дням — основа ОПиУ Ozon и листа по SKU.

Та же идея, что у отчётов WB: день начислений, дочитанный зеркалом,
складывается один раз по SKU, виду строки и типу начисления; страница читает
готовые суммы.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fr004"
down_revision = "fr003"
branch_labels = None
depends_on = None

SCHEMA = "fin_reports"
MONEY = sa.Numeric(16, 2)


def upgrade() -> None:
    op.create_table(
        "ozon_days",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "ozon_facts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sku", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("line", sa.String(16), nullable=False),
        sa.Column("type_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_price", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_commission", MONEY, nullable=False, server_default="0"),
        sa.Column("bonus", MONEY, nullable=False, server_default="0"),
        sa.Column("coinvestment", MONEY, nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_index("ix_fin_reports_ozon_facts_day", "ozon_facts", ["seller_id", "day"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("ozon_facts", schema=SCHEMA)
    op.drop_table("ozon_days", schema=SCHEMA)
