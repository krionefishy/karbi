import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd005"
down_revision = "fd004"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    op.create_table(
        "product_mappings",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chrt_id", sa.BigInteger(), primary_key=True),
        sa.Column("item_id", sa.String(128), nullable=False),
        sa.Column("characteristic", sa.String(255), server_default="", nullable=False),
        sa.Column("barcode", sa.String(64), nullable=False),
        sa.Column("article", sa.String(255), server_default="", nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_mappings_pool", "product_mappings", ["item_id", "characteristic"], schema=SCHEMA)

    op.create_table(
        "pool_seller_shares",
        sa.Column("item_id", sa.String(128), primary_key=True),
        sa.Column("characteristic", sa.String(255), primary_key=True, server_default=""),
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("share_bp", sa.Integer(), nullable=False),
        sa.CheckConstraint("share_bp BETWEEN 0 AND 10000", name="ck_wb_fbs_pool_share"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("pool_seller_shares", schema=SCHEMA)
    op.drop_table("product_mappings", schema=SCHEMA)
