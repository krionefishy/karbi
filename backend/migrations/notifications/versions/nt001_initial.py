import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "nt001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), server_default="", nullable=False),
        sa.Column("encrypted_token", sa.String(), nullable=False),
        sa.Column("token_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="notifications",
    )
    op.create_index("ix_notifications_bots_active", "bots", ["is_active"], schema="notifications")

    op.create_table(
        "bot_cursors",
        sa.Column(
            "bot_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notifications.bots.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("last_update_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="notifications",
    )

    op.create_table(
        "invite_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "bot_id", UUID(as_uuid=True), sa.ForeignKey("notifications.bots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seller_name", sa.String(255), server_default="", nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_invite_links_seller", "invite_links", ["bot_id", "seller_id"], schema="notifications"
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bot_id", UUID(as_uuid=True), sa.ForeignKey("notifications.bots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seller_name", sa.String(255), server_default="", nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(255), server_default="", nullable=False),
        sa.Column("first_name", sa.String(255), server_default="", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("bot_id", "chat_id", "seller_id", name="uq_notifications_subscription"),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_subscriptions_audience",
        "subscriptions",
        ["bot_id", "seller_id", "is_active"],
        schema="notifications",
    )

    op.create_table(
        "outgoing_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "bot_id", UUID(as_uuid=True), sa.ForeignKey("notifications.bots.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("template", sa.String(64), nullable=False),
        sa.Column("params", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'sent', 'failed')", name="ck_notifications_outgoing_status"),
        schema="notifications",
    )
    op.create_index(
        "ix_notifications_outgoing_due", "outgoing_messages", ["status", "next_attempt_at"], schema="notifications"
    )


def downgrade() -> None:
    op.drop_table("outgoing_messages", schema="notifications")
    op.drop_table("subscriptions", schema="notifications")
    op.drop_table("invite_links", schema="notifications")
    op.drop_table("bot_cursors", schema="notifications")
    op.drop_table("bots", schema="notifications")
