"""Подсорт WB: кабинеты, суточные итоги заказов, параметры расчёта, регионы складов.

Сами заказы не хранятся — только сколько штук баркода заказали за сутки в
регионе. Три месяца заказов одним ответом WB не отдаёт, поэтому сутки
сводятся по одним и складываются здесь; держим столько дней, сколько видно в
книге. Остатки по складам WB лежат в зеркале `wb_core`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "ps001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_podsort"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_error", sa.String(), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "order_days",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "order_counts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("region", sa.String(64), primary_key=True),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fbs_orders", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "barcodes",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("vendor_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject", sa.String(255), nullable=False, server_default=""),
        sa.Column("tech_size", sa.String(64), nullable=False, server_default=""),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("window_days", sa.SmallInteger(), nullable=False, server_default="7"),
        sa.Column("cover_days", sa.SmallInteger(), nullable=False, server_default="7"),
        sa.Column("regions", ARRAY(sa.String(64)), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_wb_podsort_settings_single"),
        schema=SCHEMA,
    )
    op.create_table(
        "warehouse_regions",
        sa.Column("warehouse_name", sa.String(255), primary_key=True),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in ("warehouse_regions", "settings", "barcodes", "order_counts", "order_days", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
