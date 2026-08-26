import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd002"
down_revision = "fd001"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    op.add_column(
        "tracked_sellers",
        sa.Column("warehouses_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    op.create_table(
        "wb_offices",
        sa.Column("office_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), server_default="", nullable=False),
        sa.Column("city", sa.String(255), server_default="", nullable=False),
        sa.Column("address", sa.Text(), server_default="", nullable=False),
        sa.Column("federal_district", sa.String(255), server_default="", nullable=False),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("cargo_type", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_type", sa.Integer(), server_default="0", nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_offices_city", "wb_offices", ["city"], schema=SCHEMA)

    op.create_table(
        "seller_warehouses",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("office_id", sa.BigInteger(), nullable=False),
        sa.Column("store_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(255), server_default="", nullable=False),
        sa.Column("cargo_type", sa.Integer(), server_default="0", nullable=False),
        sa.Column("delivery_type", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_deleting", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_processing", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_warehouses_office", "seller_warehouses", ["seller_id", "office_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("seller_warehouses", schema=SCHEMA)
    op.drop_table("wb_offices", schema=SCHEMA)
    op.drop_column("tracked_sellers", "warehouses_synced_at", schema=SCHEMA)
