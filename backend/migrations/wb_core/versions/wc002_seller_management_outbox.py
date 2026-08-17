import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "wc002"
down_revision = "wc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sellers",
        sa.Column("catalog_sync_status", sa.String(32), server_default="queued", nullable=False),
        schema="wb_core",
    )
    op.add_column(
        "sellers", sa.Column("last_catalog_sync_at", sa.DateTime(timezone=True), nullable=True), schema="wb_core"
    )
    op.add_column("sellers", sa.Column("catalog_sync_error", sa.String(), nullable=True), schema="wb_core")
    op.create_check_constraint(
        "ck_wb_core_sellers_catalog_sync_status",
        "sellers",
        "catalog_sync_status IN ('queued','syncing','success','error')",
        schema="wb_core",
    )
    op.create_unique_constraint(
        "uq_wb_core_credentials_fingerprint", "credentials", ["key_fingerprint"], schema="wb_core"
    )
    op.add_column(
        "articles", sa.Column("vendor_code", sa.String(255), server_default="", nullable=False), schema="wb_core"
    )
    op.add_column("articles", sa.Column("name", sa.String(512), server_default="", nullable=False), schema="wb_core")
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("message_key", sa.String(255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("locked_by", sa.String(255), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="wb_core",
    )
    op.create_index(
        "ix_wb_core_outbox_pending",
        "outbox_events",
        ["published_at", "next_attempt_at", "created_at"],
        schema="wb_core",
    )
    op.create_table(
        "inbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        schema="wb_core",
    )


def downgrade() -> None:
    op.drop_table("inbox_events", schema="wb_core")
    op.drop_index("ix_wb_core_outbox_pending", table_name="outbox_events", schema="wb_core")
    op.drop_table("outbox_events", schema="wb_core")
    op.drop_column("articles", "name", schema="wb_core")
    op.drop_column("articles", "vendor_code", schema="wb_core")
    op.drop_constraint("uq_wb_core_credentials_fingerprint", "credentials", schema="wb_core")
    op.drop_constraint("ck_wb_core_sellers_catalog_sync_status", "sellers", schema="wb_core")
    op.drop_column("sellers", "catalog_sync_error", schema="wb_core")
    op.drop_column("sellers", "last_catalog_sync_at", schema="wb_core")
    op.drop_column("sellers", "catalog_sync_status", schema="wb_core")
