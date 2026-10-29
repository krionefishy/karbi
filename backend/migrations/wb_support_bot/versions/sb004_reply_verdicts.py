import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sb004"
down_revision = "sb003"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"
CATEGORIES = (
    "threat",
    "stop_request",
    "thanks",
    "cant_delete",
    "refuses",
    "not_helped",
    "noise",
    "waiting_seller",
    "sent_details",
    "question",
    "says_deleted",
    "unclear",
)


def upgrade() -> None:
    op.add_column(
        "dialogs",
        sa.Column("reply_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    op.add_column(
        "dialogs", sa.Column("reply_cleared_through", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column("dialogs", sa.Column("reply_first_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    # Однократно: первый ответ после первого сообщения бота для уже существующих диалогов. Дальше его ведёт воркер.
    op.execute(
        """
        UPDATE wb_support_bot.dialogs d
        SET reply_first_at = (
            SELECT min(e.added_at)
            FROM wb_core.chat_events e
            WHERE e.seller_id = d.seller_id
              AND e.chat_id = d.chat_id
              AND e.sender = 'client'
              AND NOT e.review_prompt
              AND coalesce(lower(e.text), '') NOT LIKE 'мы удалили это сообщение%'
              AND e.added_at > (
                  SELECT min(s.planned_at)
                  FROM wb_support_bot.sends s
                  WHERE s.seller_id = d.seller_id
                    AND s.chat_id = d.chat_id
                    AND s.prompt_at = d.prompt_at
                    AND s.status IN ('sent', 'unknown', 'dry_run')
              )
        )
        WHERE EXISTS (
            SELECT 1 FROM wb_support_bot.sends s
            WHERE s.seller_id = d.seller_id AND s.chat_id = d.chat_id AND s.prompt_at = d.prompt_at
        )
        """
    )
    op.create_table(
        "reply_verdicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("prompt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("slice_fingerprint", sa.String(40), nullable=False),
        sa.Column("first_event_id", sa.String(64), nullable=False),
        sa.Column("last_event_id", sa.String(64), nullable=False),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="pending"),
        sa.Column("category", sa.String(24), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("guard", sa.String(16), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_wb_support_bot_reply_verdicts_status"),
        sa.CheckConstraint(
            "category IS NULL OR category IN (" + ", ".join(f"'{item}'" for item in CATEGORIES) + ")",
            name="ck_wb_support_bot_reply_verdicts_category",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_wb_support_bot_reply_verdicts_slice",
        "reply_verdicts",
        ["seller_id", "chat_id", "slice_fingerprint", "prompt_version"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_support_bot_reply_verdicts_time", "reply_verdicts", ["seller_id", "last_event_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_wb_support_bot_reply_verdicts_category",
        "reply_verdicts",
        ["seller_id", "category", "last_event_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_support_bot_reply_verdicts_status", "reply_verdicts", ["status", "requested_at"], schema=SCHEMA
    )
    op.create_table(
        "mode_changes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_support_bot_mode_changes_seller", "mode_changes", ["seller_id", "changed_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("mode_changes", schema=SCHEMA)
    op.drop_table("reply_verdicts", schema=SCHEMA)
    op.drop_column("dialogs", "reply_first_at", schema=SCHEMA)
    op.drop_column("dialogs", "reply_cleared_through", schema=SCHEMA)
    op.drop_column("dialogs", "reply_hold", schema=SCHEMA)
