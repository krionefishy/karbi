"""Расширение для кода получения: установки, коды пары, коды дня, задачи расширению."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "rt002"
down_revision = "rt001"
branch_labels = None
depends_on = None

SCHEMA = "wb_returns"


def upgrade() -> None:
    op.create_table(
        "extension_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("install_id", sa.String(64), nullable=False),
        sa.Column("browser", sa.String(255), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("paired_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_code_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deliveries", JSONB(), nullable=False, server_default="[]"),
        sa.Column("deliveries_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_returns_installs_seller", "extension_installs", ["seller_id", "revoked_at"], schema=SCHEMA)
    op.create_table(
        "pairing_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("install_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_wb_returns_pairing_live",
        "pairing_codes",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_index("ix_wb_returns_pairing_seller", "pairing_codes", ["seller_id", "expires_at"], schema=SCHEMA)
    op.create_table(
        "delivery_codes",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code_date", sa.Date(), primary_key=True),
        sa.Column("code", sa.String(16), nullable=False, server_default=""),
        sa.Column("ext_code", sa.String(16), nullable=False, server_default=""),
        sa.Column("qr", sa.String(2048), nullable=False, server_default=""),
        sa.Column("ext_qr", sa.String(2048), nullable=False, server_default=""),
        sa.Column("install_id", UUID(as_uuid=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "extension_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="refresh_code"),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_returns_tasks_open", "extension_tasks", ["seller_id", "fulfilled_at", "requested_at"], schema=SCHEMA
    )


def downgrade() -> None:
    for table in ("extension_tasks", "delivery_codes", "pairing_codes", "extension_installs"):
        op.drop_table(table, schema=SCHEMA)
