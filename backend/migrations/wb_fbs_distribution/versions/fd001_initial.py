import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("write_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="wb_fbs_distribution",
    )


def downgrade() -> None:
    op.drop_table("tracked_sellers", schema="wb_fbs_distribution")
