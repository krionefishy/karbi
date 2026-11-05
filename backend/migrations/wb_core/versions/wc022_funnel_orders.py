"""Заказы по карточке за сутки из воронки продаж — новый вид зеркала `funnel`.

Statistics `/supplier/orders` отдаёт заказов на 9–11% меньше, чем отчёты
кабинета; воронка с ними сходится. `funnel_days` отмечает прочитанные сутки:
только по ним отсутствие строки в `funnel_orders` значит «заказов не было».
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc022"
down_revision = "wc021"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', "
    "'adverts', 'ozon_accruals', 'ozon_products', 'ozon_adverts')"
)
KINDS_AFTER = (
    "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports', "
    "'adverts', 'ozon_accruals', 'ozon_products', 'ozon_adverts', 'funnel')"
)


def seller_column() -> sa.Column:
    return sa.Column(
        "seller_id",
        UUID(as_uuid=True),
        sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
        primary_key=True,
    )


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_AFTER, schema=SCHEMA)
    op.create_table(
        "funnel_days",
        seller_column(),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "funnel_orders",
        seller_column(),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("order_count", sa.Integer(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("funnel_orders", schema=SCHEMA)
    op.drop_table("funnel_days", schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind = 'funnel'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
