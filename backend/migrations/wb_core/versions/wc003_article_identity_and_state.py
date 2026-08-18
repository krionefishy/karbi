import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "wc003"
down_revision = "wc002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("imt_id", sa.BigInteger(), nullable=True), schema="wb_core")
    op.add_column("articles", sa.Column("brand", sa.String(255), server_default="", nullable=False), schema="wb_core")
    op.add_column("articles", sa.Column("subject_id", sa.Integer(), nullable=True), schema="wb_core")
    op.add_column(
        "articles", sa.Column("subject_name", sa.String(255), server_default="", nullable=False), schema="wb_core"
    )
    op.add_column(
        "articles", sa.Column("photo_url", sa.String(1024), server_default="", nullable=False), schema="wb_core"
    )
    op.add_column(
        "articles",
        sa.Column("sizes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        schema="wb_core",
    )
    # A single is_active flag could not tell "seller archived it" from "we only
    # ever saw it in feedbacks", so both syncs kept overwriting each other.
    op.add_column(
        "articles", sa.Column("state", sa.String(16), server_default="active", nullable=False), schema="wb_core"
    )
    op.execute("UPDATE wb_core.articles SET state = 'active' WHERE is_active")
    op.execute("UPDATE wb_core.articles SET state = 'feedback_only' WHERE NOT is_active")
    op.create_check_constraint(
        "ck_wb_core_articles_state",
        "articles",
        "state IN ('active', 'archived', 'feedback_only')",
        schema="wb_core",
    )
    op.drop_index("ix_wb_core_articles_seller_active", table_name="articles", schema="wb_core")
    op.drop_column("articles", "is_active", schema="wb_core")
    op.create_index("ix_wb_core_articles_seller_state", "articles", ["seller_id", "state"], schema="wb_core")
    op.create_index("ix_wb_core_articles_seller_imt", "articles", ["seller_id", "imt_id"], schema="wb_core")


def downgrade() -> None:
    op.add_column(
        "articles", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False), schema="wb_core"
    )
    op.execute("UPDATE wb_core.articles SET is_active = (state = 'active')")
    op.drop_index("ix_wb_core_articles_seller_imt", table_name="articles", schema="wb_core")
    op.drop_index("ix_wb_core_articles_seller_state", table_name="articles", schema="wb_core")
    op.drop_constraint("ck_wb_core_articles_state", "articles", schema="wb_core")
    op.create_index("ix_wb_core_articles_seller_active", "articles", ["seller_id", "is_active"], schema="wb_core")
    for column in ("state", "sizes", "photo_url", "subject_name", "subject_id", "brand", "imt_id"):
        op.drop_column("articles", column, schema="wb_core")
