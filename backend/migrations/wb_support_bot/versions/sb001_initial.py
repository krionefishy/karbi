"""Бот поддержки в чатах WB: кабинеты и режим, выключатель, тексты, диалоги, журнал отправок.

События чатов лежат в зеркале `wb_core`; здесь только то, что бот решил и сделал:
состояние диалога с расписанием рассылки и журнал отправок. Журнал пишется до
обращения к WB, поэтому по нему видно и отправленное, и «неизвестно, ушло ли».
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "sb001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mode", sa.String(16), server_default="dry_run", nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("mode IN ('dry_run', 'live')", name="ck_wb_support_bot_tracked_mode"),
        schema=SCHEMA,
    )
    op.create_table(
        "control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paused", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.execute(f"INSERT INTO {SCHEMA}.control (id, paused) VALUES (1, false)")
    op.create_table(
        "templates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_support_bot_templates_seller_id", "templates", ["seller_id"], schema=SCHEMA)
    op.create_table(
        "dialogs",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("chat_id", sa.String(64), primary_key=True),
        sa.Column("prompt_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("buyer_first_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("buyer_last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("our_last_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campaign_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campaigns_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("no_contact", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("attention", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("excluded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("closed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_support_bot_dialogs_due", "dialogs", ["seller_id", "state", "next_send_at"], schema=SCHEMA)
    op.create_index("ix_wb_support_bot_dialogs_state", "dialogs", ["seller_id", "state", "updated_at"], schema=SCHEMA)
    op.create_table(
        "sends",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("prompt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('planned', 'sending', 'sent', 'unknown', 'failed', 'dry_run')",
            name="ck_wb_support_bot_sends_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_wb_support_bot_sends_slot",
        "sends",
        ["seller_id", "chat_id", "prompt_at", "seq"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index("ix_wb_support_bot_sends_seller_time", "sends", ["seller_id", "planned_at"], schema=SCHEMA)


def downgrade() -> None:
    for table in ("sends", "dialogs", "templates", "control", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
