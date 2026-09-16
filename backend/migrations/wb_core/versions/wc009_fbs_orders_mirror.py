"""Зеркало FBS-логистики: склады продавца, объекты WB, сборочные задания, поставки.

Нужно автоматизации штрафов: по номеру задания из фин. отчёта найти склад
продавца и поставку. По правилу зеркала лежит в `wb_core` — это копия ответов
WB по селлеру, а не расчёт. Заданий много (у крупного кабинета ~1000 в день),
поэтому у них три индекса под ключи, которыми их ищут: `rid` (= `srid`
отчёта), стикер и дата.

`fbs_order_archive_months` — какие архивные месяцы уже прочитаны: архив WB
неизменяем, и второй раз его читать незачем.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc009"
down_revision = "wc008"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def _seller_column() -> sa.Column:
    return sa.Column(
        "seller_id", UUID(as_uuid=True), sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_wb_core_mirror_state_kind",
        "mirror_state",
        "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies')",
        schema=SCHEMA,
    )
    op.create_table(
        "seller_warehouses",
        _seller_column(),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("office_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "wb_offices",
        sa.Column("office_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("city", sa.String(255), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "fbs_orders",
        _seller_column(),
        sa.Column("order_id", sa.BigInteger(), primary_key=True),
        sa.Column("rid", sa.String(64), nullable=False, server_default=""),
        sa.Column("order_uid", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("supply_id", sa.String(32), nullable=True),
        sa.Column("office_id", sa.BigInteger(), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("chrt_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sku", sa.String(64), nullable=False, server_default=""),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sticker_id", sa.BigInteger(), nullable=True),
        sa.Column("supplier_status", sa.String(32), nullable=True),
        sa.Column("wb_status", sa.String(32), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('live', 'archive')", name="ck_wb_core_fbs_orders_source"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_fbs_orders_rid", "fbs_orders", ["seller_id", "rid"], schema=SCHEMA)
    op.create_index("ix_wb_core_fbs_orders_sticker", "fbs_orders", ["seller_id", "sticker_id"], schema=SCHEMA)
    op.create_index("ix_wb_core_fbs_orders_created", "fbs_orders", ["seller_id", "created_at"], schema=SCHEMA)
    op.create_table(
        "fbs_order_archive_months",
        _seller_column(),
        sa.Column("year", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Integer(), primary_key=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "fbs_supplies",
        _seller_column(),
        sa.Column("supply_id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scan_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destination_office_id", sa.BigInteger(), nullable=True),
        sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cargo_type", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in ("fbs_supplies", "fbs_order_archive_months", "fbs_orders", "wb_offices", "seller_warehouses"):
        op.drop_table(table, schema=SCHEMA)
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_wb_core_mirror_state_kind", "mirror_state", "kind IN ('catalog', 'stocks', 'reviews')", schema=SCHEMA
    )
