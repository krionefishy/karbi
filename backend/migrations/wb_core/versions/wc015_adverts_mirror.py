"""Зеркало рекламы WB: кампании, списания по дням и статистика по артикулам.

Удержание за «WB Продвижение» в отчёте реализации идёт одной строкой без
артикула. Чтобы разложить рекламу по товарам, нужны сам рекламный API:
списания по кампаниям с типом оплаты (`/adv/v1/upd`), статистика кампаний по
артикулам и дням (`/adv/v3/fullstats`) и состав кампаний
(`/api/advert/v2/adverts`). По правилу зеркала всё это — копия ответов WB.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "wc015"
down_revision = "wc014"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports')"
KINDS_AFTER = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', 'adverts')"
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
        "advert_campaigns",
        seller_column(),
        sa.Column("advert_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payment_type", sa.String(16), nullable=False, server_default=""),
        sa.Column("bid_type", sa.String(16), nullable=False, server_default=""),
        sa.Column("nm_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "advert_spend",
        seller_column(),
        sa.Column("advert_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("payment_type", sa.String(32), primary_key=True),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_advert_spend_day", "advert_spend", ["seller_id", "day"], schema=SCHEMA)
    op.create_table(
        "advert_nm_stats",
        seller_column(),
        sa.Column("advert_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("nm_id", sa.BigInteger(), primary_key=True),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("atbs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("canceled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("orders_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_advert_nm_stats_day", "advert_nm_stats", ["seller_id", "day"], schema=SCHEMA)
    op.create_table(
        "advert_cursors",
        seller_column(),
        sa.Column("collected_through", sa.Date(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in ("advert_cursors", "advert_nm_stats", "advert_spend", "advert_campaigns"):
        op.drop_table(table, schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind = 'adverts'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
