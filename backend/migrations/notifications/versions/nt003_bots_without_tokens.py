"""Move bot tokens off this server, keep only the invite link template.

Tokens now live on the relay, which is the only host that can reach the
messenger. The table is empty in every environment, so this is a plain column
change with nothing to migrate and no downtime.

Revision ID: nt003
Revises: nt002
"""

import sqlalchemy as sa
from alembic import op

revision = "nt003"
down_revision = "nt002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("invite_link_template", sa.String(length=512), nullable=False, server_default=""),
        schema="notifications",
    )
    op.drop_column("bots", "username", schema="notifications")
    op.drop_column("bots", "encrypted_token", schema="notifications")
    op.drop_column("bots", "token_fingerprint", schema="notifications")
    # The messenger's own id for a sent message is opaque to this server now:
    # the relay returns a reference, not necessarily a number.
    op.alter_column(
        "outgoing_messages",
        "telegram_message_id",
        new_column_name="message_ref",
        type_=sa.String(length=64),
        postgresql_using="telegram_message_id::varchar",
        schema="notifications",
    )


def downgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("token_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        schema="notifications",
    )
    op.add_column(
        "bots", sa.Column("encrypted_token", sa.String(), nullable=False, server_default=""), schema="notifications"
    )
    op.add_column(
        "bots", sa.Column("username", sa.String(length=64), nullable=False, server_default=""), schema="notifications"
    )
    op.create_unique_constraint(
        "uq_notifications_bots_token_fingerprint", "bots", ["token_fingerprint"], schema="notifications"
    )
    op.alter_column(
        "outgoing_messages",
        "message_ref",
        new_column_name="telegram_message_id",
        type_=sa.BigInteger(),
        postgresql_using="message_ref::bigint",
        schema="notifications",
    )
    op.drop_column("bots", "invite_link_template", schema="notifications")
