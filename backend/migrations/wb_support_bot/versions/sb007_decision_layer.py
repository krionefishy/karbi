import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sb007"
down_revision = "sb006"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"


def upgrade() -> None:
    op.add_column("reply_verdicts", sa.Column("needs_manager", sa.Boolean(), nullable=True), schema=SCHEMA)
    op.add_column("reply_verdicts", sa.Column("decision_path", sa.String(10), nullable=True), schema=SCHEMA)
    op.add_column("reply_verdicts", sa.Column("decision_votes", postgresql.JSONB(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("reply_verdicts", "decision_votes", schema=SCHEMA)
    op.drop_column("reply_verdicts", "decision_path", schema=SCHEMA)
    op.drop_column("reply_verdicts", "needs_manager", schema=SCHEMA)
