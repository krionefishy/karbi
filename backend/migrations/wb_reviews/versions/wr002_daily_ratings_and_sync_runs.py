import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "wr002"
down_revision = "wr001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The old table has not been populated in production yet and its single total
    # cannot be converted into a truthful 1–5 star distribution.
    op.drop_table("daily_review_counts", schema="wb_reviews")
    op.create_table(
        "daily_review_counts",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article", sa.String(255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count_rating_1", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_rating_2", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_rating_3", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_rating_4", sa.Integer(), server_default="0", nullable=False),
        sa.Column("count_rating_5", sa.Integer(), server_default="0", nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "count_rating_1 >= 0 AND count_rating_2 >= 0 AND count_rating_3 >= 0 "
            "AND count_rating_4 >= 0 AND count_rating_5 >= 0",
            name="ck_wb_reviews_daily_counts_non_negative",
        ),
        sa.PrimaryKeyConstraint("seller_id", "article", "date"),
        schema="wb_reviews",
    )
    op.create_index(
        "ix_wb_reviews_daily_seller_article_date",
        "daily_review_counts",
        ["seller_id", "article", "date"],
        schema="wb_reviews",
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completed_sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("trigger IN ('scheduled','manual')", name="ck_wb_reviews_sync_runs_trigger"),
        sa.CheckConstraint(
            "status IN ('queued','running','success','partial_success','error')",
            name="ck_wb_reviews_sync_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="wb_reviews",
    )
    op.create_index("ix_wb_reviews_sync_runs_created", "sync_runs", ["created_at"], schema="wb_reviews")
    op.create_table(
        "sync_run_sellers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("product_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("feedback_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','success','error')",
            name="ck_wb_reviews_sync_run_sellers_status",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["wb_reviews.sync_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seller_id", name="uq_wb_reviews_sync_run_seller"),
        schema="wb_reviews",
    )
    op.create_index(
        "ix_wb_reviews_sync_run_sellers_run_status",
        "sync_run_sellers",
        ["run_id", "status"],
        schema="wb_reviews",
    )


def downgrade() -> None:
    op.drop_table("sync_run_sellers", schema="wb_reviews")
    op.drop_table("sync_runs", schema="wb_reviews")
    op.drop_table("daily_review_counts", schema="wb_reviews")
    op.create_table(
        "daily_review_counts",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article", sa.String(255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count_reviews", sa.Integer(), nullable=False),
        sa.CheckConstraint("count_reviews >= 0", name="ck_wb_reviews_daily_count_non_negative"),
        sa.PrimaryKeyConstraint("seller_id", "article", "date"),
        schema="wb_reviews",
    )
