import sqlalchemy as sa
from alembic import op

revision = "cd003"
down_revision = "cd002"
branch_labels = None
depends_on = None

SCHEMA = "wb_card_documents"


def upgrade() -> None:
    op.add_column("tracked_sellers", sa.Column("cursor_updated_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("tracked_sellers", sa.Column("cursor_nm_id", sa.BigInteger()), schema=SCHEMA)
    op.add_column("tracked_sellers", sa.Column("full_collected_at", sa.DateTime(timezone=True)), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("tracked_sellers", "full_collected_at", schema=SCHEMA)
    op.drop_column("tracked_sellers", "cursor_nm_id", schema=SCHEMA)
    op.drop_column("tracked_sellers", "cursor_updated_at", schema=SCHEMA)
