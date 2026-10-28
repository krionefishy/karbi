import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wt005"
down_revision = "wt004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("barcode", sa.String(64), server_default="", nullable=False),
        schema="wb_turnover",
    )
    op.create_index(
        "ix_wb_turnover_orders_barcode",
        "orders",
        ["seller_id", "barcode", "order_date"],
        schema="wb_turnover",
    )
    op.execute(
        """
        UPDATE wb_turnover.tracked_sellers
           SET orders_watermark = LEAST(orders_watermark, now() - interval '8 days')
         WHERE orders_watermark IS NOT NULL
        """
    )
    op.create_table(
        "board_items",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("nomenclature_1c", sa.String(500), server_default="", nullable=False),
        sa.Column("note", sa.String(2000), server_default="", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("added_by", UUID(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("note_updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_turnover",
    )


def downgrade() -> None:
    op.drop_table("board_items", schema="wb_turnover")
    op.drop_index("ix_wb_turnover_orders_barcode", table_name="orders", schema="wb_turnover")
    op.drop_column("orders", "barcode", schema="wb_turnover")
