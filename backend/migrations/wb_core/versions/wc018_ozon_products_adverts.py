"""Карточки Ozon и расход рекламы Ozon по SKU — два новых вида зеркала.

Карточки — чтобы в отчётах по SKU был артикул продавца и название; расход
рекламы из Performance API — чтобы разложить рекламу Ozon по товарам: в
начислениях она приходит одной суммой на кабинет.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc018"
down_revision = "wc017"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', "
    "'adverts', 'ozon_accruals')"
)
KINDS_AFTER = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', "
    "'adverts', 'ozon_accruals', 'ozon_products', 'ozon_adverts')"
)
MONEY = sa.Numeric(14, 2)


def seller_column() -> sa.Column:
    return sa.Column(
        "seller_id", UUID(as_uuid=True), sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_AFTER, schema=SCHEMA)
    op.create_table(
        "ozon_products",
        seller_column(),
        sa.Column("sku", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("offer_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "ozon_advert_campaigns",
        seller_column(),
        sa.Column("campaign_id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(64), nullable=False, server_default=""),
        sa.Column("object_type", sa.String(64), nullable=False, server_default=""),
        sa.Column("payment_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("created_at", sa.Date(), nullable=True),
        sa.Column("from_date", sa.Date(), nullable=True),
        sa.Column("to_date", sa.Date(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "ozon_advert_stats",
        seller_column(),
        sa.Column("campaign_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("sku", sa.BigInteger(), primary_key=True),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("money_spent", MONEY, nullable=False, server_default="0"),
        sa.Column("orders_money", MONEY, nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_ozon_advert_stats_day", "ozon_advert_stats", ["seller_id", "day"], schema=SCHEMA)
    op.create_table(
        "ozon_advert_cursors",
        seller_column(),
        sa.Column("collected_through", sa.Date(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind IN ('ozon_products', 'ozon_adverts')")
    op.drop_table("ozon_advert_cursors", schema=SCHEMA)
    op.drop_table("ozon_advert_stats", schema=SCHEMA)
    op.drop_table("ozon_advert_campaigns", schema=SCHEMA)
    op.drop_table("ozon_products", schema=SCHEMA)
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
