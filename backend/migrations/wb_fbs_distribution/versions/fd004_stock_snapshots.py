import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd004"
down_revision = "fd003"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    op.create_table(
        "stock_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lines", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('accepted', 'rejected')", name="ck_wb_fbs_snapshot_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_snapshots_received", "stock_snapshots", ["received_at"], schema=SCHEMA)

    op.create_table(
        "stock_pools",
        sa.Column("item_id", sa.String(128), primary_key=True),
        sa.Column("characteristic", sa.String(255), primary_key=True, server_default=""),
        sa.Column("barcode", sa.String(64), server_default="", nullable=False),
        sa.Column("name", sa.String(512), server_default="", nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_wb_fbs_pool_quantity"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_pools_barcode", "stock_pools", ["barcode"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("stock_pools", schema=SCHEMA)
    op.drop_table("stock_snapshots", schema=SCHEMA)
