"""Спрос по округам и остатки FBS в разрезе склада — сырьё для отчёта подсорта.

Заказы живут полтора месяца, поэтому долю округа в спросе за полгода нельзя
собрать запросом к ним: нужен агрегат, который переживает чистку. Он собирается
у WB посуточно и хранит границы своего отрезка, а не считается из `orders` —
за горизонтом чистки такой пересчёт обнулил бы догруженную историю.

Остатки по складам, наоборот, истории не требуют: подсорт спрашивает «что лежит
сегодня», и слой перезаписывается целиком.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wt004"
down_revision = "wt003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("regions_filled_from", "regions_filled_to"):
        op.add_column("tracked_sellers", sa.Column(column, sa.Date(), nullable=True), schema="wb_turnover")
    op.add_column(
        "tracked_sellers",
        sa.Column("fbs_stocks_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_turnover",
    )
    op.create_table(
        "region_orders",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("district", sa.String(64), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled", sa.Integer(), server_default="0", nullable=False),
        schema="wb_turnover",
    )
    op.create_index("ix_wb_turnover_region_orders_window", "region_orders", ["seller_id", "date"], schema="wb_turnover")
    op.create_table(
        "warehouse_stocks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        schema="wb_turnover",
    )


def downgrade() -> None:
    op.drop_table("warehouse_stocks", schema="wb_turnover")
    op.drop_table("region_orders", schema="wb_turnover")
    for column in ("fbs_stocks_at", "regions_filled_to", "regions_filled_from"):
        op.drop_column("tracked_sellers", column, schema="wb_turnover")
