import sqlalchemy as sa
from alembic import op

revision = "sb010"
down_revision = "sb009"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"
COLUMNS = ("interval_minutes", "jitter_min_minutes", "jitter_max_minutes")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column("tracked_sellers", sa.Column(name, sa.Integer(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    for name in reversed(COLUMNS):
        op.drop_column("tracked_sellers", name, schema=SCHEMA)
