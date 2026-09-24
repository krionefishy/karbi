"""Зеркало отчёта «Остатки на складах»: баркод × склад WB.

Отчёт аналитики, из которого берётся FBO для `stock_facts`, склады склеивает
в один «Склад WB». Подсорту нужно знать, сколько товара уже лежит в регионе,
поэтому рядом лежит второй отчёт — с разбивкой по складам. По правилу зеркала
это копия ответа WB: переписывается целиком, служебные строки отчёта хранятся
как пришли.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc011"
down_revision = "wc010"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats')"
KINDS_AFTER = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains')"


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_AFTER, schema=SCHEMA)
    op.create_table(
        "warehouse_remains",
        sa.Column(
            "seller_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("warehouse_name", sa.String(255), primary_key=True),
        sa.Column("article", sa.String(255), nullable=False),
        sa.Column("tech_size", sa.String(64), nullable=False, server_default=""),
        sa.Column("vendor_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("warehouse_remains", schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind = 'remains'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
