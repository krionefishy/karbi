import sqlalchemy as sa
from alembic import op

revision = "cd004"
down_revision = "cd003"
branch_labels = None
depends_on = None

SCHEMA = "wb_card_documents"


def upgrade() -> None:
    op.add_column(
        "card_states",
        sa.Column("episode_alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.card_states SET episode_alerted = true WHERE state <> 'ok' AND closed_at IS NULL")


def downgrade() -> None:
    op.drop_column("card_states", "episode_alerted", schema=SCHEMA)
