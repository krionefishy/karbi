import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wt006"
down_revision = "wt005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tracked_sellers",
        sa.Column("fbo_stocks_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_turnover",
    )
    op.create_table(
        "fbo_stocks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chrt_id", sa.BigInteger(), primary_key=True),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        schema="wb_turnover",
    )


def downgrade() -> None:
    op.drop_table("fbo_stocks", schema="wb_turnover")
    op.drop_column("tracked_sellers", "fbo_stocks_at", schema="wb_turnover")
