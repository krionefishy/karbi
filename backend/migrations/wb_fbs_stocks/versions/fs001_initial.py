import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fs001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_stocks"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_error", sa.String(), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "warehouses",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("office_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("delivery_type", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deleting", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "groups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="district"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("kind IN ('own', 'fulfilment', 'district')", name="ck_wb_fbs_stocks_group_kind"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_stocks_groups_seller", "groups", ["seller_id", "position"], schema=SCHEMA)
    op.create_table(
        "columns",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "group_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_stocks_columns_group", "columns", ["group_id", "position"], schema=SCHEMA)
    op.create_table(
        "barcodes",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("note", sa.String(2000), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_by", UUID(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("note_updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("note_updated_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "stock_facts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("barcode", sa.String(64), primary_key=True),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "refresh_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_fbs_stocks_refresh_status"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_stocks_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema=SCHEMA)
    op.create_index(
        "uq_wb_fbs_stocks_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_wb_fbs_stocks_refresh_active", table_name="refresh_requests", schema=SCHEMA)
    op.drop_index("ix_wb_fbs_stocks_refresh_seller", table_name="refresh_requests", schema=SCHEMA)
    op.drop_index("ix_wb_fbs_stocks_columns_group", table_name="columns", schema=SCHEMA)
    op.drop_index("ix_wb_fbs_stocks_groups_seller", table_name="groups", schema=SCHEMA)
    for table in ("refresh_requests", "stock_facts", "barcodes", "columns", "groups", "warehouses", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
