import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd006"
down_revision = "fd005"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    op.create_table(
        "allocation_plans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reserve_units", sa.Integer(), nullable=False),
        sa.Column("priority_regions", sa.Integer(), nullable=False),
        sa.Column("warehouses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped", sa.Integer(), server_default="0", nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_plans_seller", "allocation_plans", ["seller_id", "created_at"], schema=SCHEMA)

    op.create_table(
        "allocation_items",
        sa.Column("plan_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chrt_id", sa.BigInteger(), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.CheckConstraint("amount >= 0", name="ck_wb_fbs_item_amount"),
        schema=SCHEMA,
    )

    op.create_table(
        "allocation_skips",
        sa.Column("plan_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chrt_id", sa.BigInteger(), primary_key=True),
        sa.Column("item_id", sa.String(128), nullable=False),
        sa.Column("characteristic", sa.String(255), server_default="", nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("allocation_skips", schema=SCHEMA)
    op.drop_table("allocation_items", schema=SCHEMA)
    op.drop_table("allocation_plans", schema=SCHEMA)
