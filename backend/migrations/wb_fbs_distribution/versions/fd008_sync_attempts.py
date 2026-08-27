import sqlalchemy as sa
from alembic import op

revision = "fd008"
down_revision = "fd007"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    # Время последней попытки, а не последнего успеха. Без него кабинет с
    # отозванным ключом оставался «просроченным» и переспрашивал WB каждый
    # оборот воркера, выедая общий бюджет запросов.
    op.add_column(
        "tracked_sellers",
        sa.Column("warehouses_sync_attempted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("tracked_sellers", "warehouses_sync_attempted_at", schema=SCHEMA)
