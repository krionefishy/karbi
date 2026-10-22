import sqlalchemy as sa
from alembic import op

revision = "cd002"
down_revision = "cd001"
branch_labels = None
depends_on = None

SCHEMA = "wb_card_documents"


def upgrade() -> None:
    op.add_column(
        "tracked_sellers",
        sa.Column("collection_error_permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("tracked_sellers", "collection_error_permanent", schema=SCHEMA)
