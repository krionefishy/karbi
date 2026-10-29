import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sb006"
down_revision = "sb005"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"


def upgrade() -> None:
    op.create_table(
        "monitor_state",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overall", sa.String(8), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "monitor_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("check_id", sa.String(48), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_support_bot_monitor_events_seller", "monitor_events", ["seller_id", "started_at"], schema=SCHEMA
    )
    op.create_index("ix_wb_support_bot_monitor_events_open", "monitor_events", ["seller_id", "ended_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("monitor_events", schema=SCHEMA)
    op.drop_table("monitor_state", schema=SCHEMA)
