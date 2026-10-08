"""Зеркало начислений Ozon: строки `/v1/finance/accrual/by-day` и курсор по дням.

Список транзакций Ozon отключён 8 сентября 2026, его замена — начисления за
день: отправление, товар или услуга, внутри — суммы с типами из справочника.
Храним построчно, как ответ Ozon; складывает и раскладывает по статьям модуль.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc016"
down_revision = "wc015"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', 'adverts')"
)
KINDS_AFTER = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', "
    "'adverts', 'ozon_accruals')"
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
        "ozon_accrual_lines",
        seller_column(),
        sa.Column("accrual_id", sa.BigInteger(), primary_key=True),
        sa.Column("line_no", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default=""),
        sa.Column("unit_number", sa.String(64), nullable=False, server_default=""),
        sa.Column("delivery_schema", sa.String(16), nullable=False, server_default=""),
        sa.Column("line", sa.String(16), nullable=False),
        sa.Column("sku", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("type_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("amount", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_price", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_commission", MONEY, nullable=False, server_default="0"),
        sa.Column("bonus", MONEY, nullable=False, server_default="0"),
        sa.Column("coinvestment", MONEY, nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_ozon_accrual_lines_day", "ozon_accrual_lines", ["seller_id", "day"], schema=SCHEMA)
    op.create_table(
        "ozon_accrual_cursors",
        seller_column(),
        sa.Column("collected_through", sa.Date(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("ozon_accrual_cursors", schema=SCHEMA)
    op.drop_table("ozon_accrual_lines", schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind = 'ozon_accruals'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
