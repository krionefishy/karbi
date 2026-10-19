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
