import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "cc001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_card_checklist"


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
        "card_facts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("vendor_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(64), nullable=False, server_default=""),
        sa.Column("imt_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_id", sa.Integer(), nullable=True),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("photo_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_video", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("characteristic_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("card_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "subject_characteristics",
        sa.Column("subject_id", sa.Integer(), primary_key=True),
        sa.Column("characteristics", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "price_facts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discounted_price", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("discount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("club_discount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "marks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("item", sa.String(64), primary_key=True),
        sa.Column("checked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "comments",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("text", sa.String(2000), nullable=False, server_default=""),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_card_checklist_refresh_status"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_card_checklist_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema=SCHEMA
    )
    op.create_index(
        "uq_wb_card_checklist_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_wb_card_checklist_refresh_active", table_name="refresh_requests", schema=SCHEMA)
    op.drop_index("ix_wb_card_checklist_refresh_seller", table_name="refresh_requests", schema=SCHEMA)
    for table in (
        "refresh_requests",
        "comments",
        "marks",
        "price_facts",
        "subject_characteristics",
        "card_facts",
        "tracked_sellers",
    ):
        op.drop_table(table, schema=SCHEMA)
