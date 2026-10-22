import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "cd001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_card_documents"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_error", sa.String(), nullable=True),
        sa.Column("baseline_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "card_states",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nm_id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_code", sa.String(255), nullable=False, server_default=""),
        sa.Column("title", sa.String(512), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("document_number", sa.String(255), nullable=True),
        sa.Column("document_start", sa.Date(), nullable=True),
        sa.Column("document_end", sa.Date(), nullable=True),
        sa.Column("document_endless", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("threshold", sa.Integer(), nullable=True),
        sa.Column("verdict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("overall_status", sa.Integer(), nullable=True),
        sa.Column("overall_reason", sa.String(255), nullable=True),
        sa.Column("overall_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("documents", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("declared", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("since", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('ok', 'rejected', 'missing', 'expiring', 'mismatch')", name="ck_wb_card_documents_state"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "transitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("from_state", sa.String(16), nullable=True),
        sa.Column("from_reason", sa.String(255), nullable=True),
        sa.Column("to_state", sa.String(16), nullable=False),
        sa.Column("to_reason", sa.String(255), nullable=True),
        sa.Column("threshold", sa.Integer(), nullable=True),
        sa.Column("document_number", sa.String(255), nullable=True),
        sa.Column("verdict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("happened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_card_documents_transitions_seller", "transitions", ["seller_id", "happened_at"], schema=SCHEMA
    )
    op.create_table(
        "notification_log",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
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
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_card_documents_refresh_status"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_card_documents_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema=SCHEMA
    )
    op.create_index(
        "uq_wb_card_documents_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_wb_card_documents_refresh_active", table_name="refresh_requests", schema=SCHEMA)
    op.drop_index("ix_wb_card_documents_refresh_seller", table_name="refresh_requests", schema=SCHEMA)
    op.drop_index("ix_wb_card_documents_transitions_seller", table_name="transitions", schema=SCHEMA)
    for table in ("refresh_requests", "notification_log", "transitions", "card_states", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
