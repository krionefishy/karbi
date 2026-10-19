import sqlalchemy as sa
from alembic import op

revision = "fp002"
down_revision = "fp001"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_penalties"


def upgrade() -> None:
    op.add_column("tracked_sellers", sa.Column("backfill_from", sa.Date(), nullable=True), schema=SCHEMA)
    op.add_column("tracked_sellers", sa.Column("backfill_to", sa.Date(), nullable=True), schema=SCHEMA)
    op.add_column(
        "tracked_sellers",
        sa.Column("backfill_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.add_column(
        "tracked_sellers",
        sa.Column("backfill_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for column in ("backfill_done", "backfill_cursor", "backfill_to", "backfill_from"):
        op.drop_column("tracked_sellers", column, schema=SCHEMA)
