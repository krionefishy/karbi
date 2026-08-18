import sqlalchemy as sa
from alembic import op

revision = "wr003"
down_revision = "wr002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_run_sellers",
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        schema="wb_reviews",
    )
    op.add_column(
        "sync_run_sellers",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_reviews",
    )
    op.add_column(
        "sync_run_sellers",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_reviews",
    )
    op.create_index(
        "ix_wb_reviews_sync_run_sellers_due", "sync_run_sellers", ["status", "next_attempt_at"], schema="wb_reviews"
    )
    op.create_index(
        "ix_wb_reviews_sync_run_sellers_lease", "sync_run_sellers", ["status", "lease_expires_at"], schema="wb_reviews"
    )
    # Jobs left running by the old code had no lease and would block every later
    # run forever; close them so the first deploy starts from a clean slate.
    op.execute(
        "UPDATE wb_reviews.sync_run_sellers SET status = 'error', finished_at = now(), "
        "error = COALESCE(error, 'Прервано при обновлении сервиса') "
        "WHERE status IN ('queued', 'running')"
    )
    op.execute(
        "UPDATE wb_reviews.sync_runs SET status = 'error', finished_at = now() WHERE status IN ('queued', 'running')"
    )


def downgrade() -> None:
    op.drop_index("ix_wb_reviews_sync_run_sellers_lease", table_name="sync_run_sellers", schema="wb_reviews")
    op.drop_index("ix_wb_reviews_sync_run_sellers_due", table_name="sync_run_sellers", schema="wb_reviews")
    for column in ("lease_expires_at", "next_attempt_at", "attempts"):
        op.drop_column("sync_run_sellers", column, schema="wb_reviews")
