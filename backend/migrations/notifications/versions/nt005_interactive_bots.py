import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "nt005"
down_revision = "nt004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("interactive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema="notifications",
    )
    op.add_column(
        "outgoing_messages",
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="message"),
        schema="notifications",
    )
    op.add_column("outgoing_messages", sa.Column("buttons", JSONB(), nullable=True), schema="notifications")
    op.add_column(
        "outgoing_messages", sa.Column("reply_to", sa.String(length=64), nullable=True), schema="notifications"
    )
    op.add_column(
        "outgoing_messages", sa.Column("target_ref", sa.String(length=64), nullable=True), schema="notifications"
    )
    op.create_check_constraint(
        "ck_notifications_outgoing_kind",
        "outgoing_messages",
        "kind IN ('message', 'edit', 'callback_answer')",
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_constraint("ck_notifications_outgoing_kind", "outgoing_messages", schema="notifications", type_="check")
    op.drop_column("outgoing_messages", "target_ref", schema="notifications")
    op.drop_column("outgoing_messages", "reply_to", schema="notifications")
    op.drop_column("outgoing_messages", "buttons", schema="notifications")
    op.drop_column("outgoing_messages", "kind", schema="notifications")
    op.drop_column("bots", "interactive", schema="notifications")
