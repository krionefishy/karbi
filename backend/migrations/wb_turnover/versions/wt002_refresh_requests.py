import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wt002"
down_revision = "wt001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'success', 'error')", name="ck_wb_turnover_refresh_status"),
        schema="wb_turnover",
    )
    op.create_index(
        "ix_wb_turnover_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema="wb_turnover"
    )
    # One outstanding request per seller: the button may be pressed twice, but
    # Wildberries must not be asked twice for it.
    op.create_index(
        "uq_wb_turnover_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema="wb_turnover",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("refresh_requests", schema="wb_turnover")
