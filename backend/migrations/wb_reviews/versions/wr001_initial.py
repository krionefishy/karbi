import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "wr001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS wb_reviews")
    op.create_table(
        "daily_review_counts",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count_reviews", sa.Integer(), nullable=False),
        sa.CheckConstraint("count_reviews >= 0", name="ck_wb_reviews_daily_count_non_negative"),
        sa.PrimaryKeyConstraint("seller_id", "article", "date"),
        schema="wb_reviews",
    )


def downgrade() -> None:
    op.drop_table("daily_review_counts", schema="wb_reviews")
