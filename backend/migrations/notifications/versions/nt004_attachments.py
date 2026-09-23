"""Вложение к исходящему сообщению: картинка QR рядом с текстом."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "nt004"
down_revision = "nt003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outgoing_messages", sa.Column("attachment", JSONB(), nullable=True), schema="notifications")


def downgrade() -> None:
    op.drop_column("outgoing_messages", "attachment", schema="notifications")
