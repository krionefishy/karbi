import sqlalchemy as sa
from alembic import op

revision = "nt002"
down_revision = "nt001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # At most one live link per (bot, seller): concurrent create_invite calls
    # race past each other's revoke, and only the database can arbitrate.
    op.create_index(
        "uq_notifications_invite_links_live",
        "invite_links",
        ["bot_id", "seller_id"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
        schema="notifications",
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_invite_links_live", table_name="invite_links", schema="notifications")
