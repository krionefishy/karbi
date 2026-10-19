from alembic import op

revision = "wr005"
down_revision = "wr004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_wb_reviews_daily_seller_article_date",
        table_name="daily_review_counts",
        schema="wb_reviews",
    )


def downgrade() -> None:
    op.create_index(
        "ix_wb_reviews_daily_seller_article_date",
        "daily_review_counts",
        ["seller_id", "article", "date"],
        schema="wb_reviews",
    )
