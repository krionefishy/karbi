import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wr004"
down_revision = "wr003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="wb_reviews",
    )
    # Membership used to be implicit: the run covered every seller in the registry.
    # Enrolling all of them keeps the first deploy behaving exactly as before.
    # Reading wb_core here is a one-time backfill, not a runtime dependency.
    op.execute(
        "INSERT INTO wb_reviews.tracked_sellers (seller_id) "
        "SELECT id FROM wb_core.sellers WHERE archived_at IS NULL "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("tracked_sellers", schema="wb_reviews")
