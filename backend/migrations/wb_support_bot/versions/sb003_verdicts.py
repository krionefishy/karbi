import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sb003"
down_revision = "sb002"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"


def upgrade() -> None:
    op.add_column("dialogs", sa.Column("tail_event_id", sa.String(64), nullable=True), schema=SCHEMA)
    op.add_column("dialogs", sa.Column("tail_fingerprint", sa.String(40), nullable=True), schema=SCHEMA)
    op.add_column("dialogs", sa.Column("manual_verdict", sa.String(8), nullable=True), schema=SCHEMA)
    op.add_column("dialogs", sa.Column("manual_fingerprint", sa.String(40), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        "ck_wb_support_bot_dialogs_manual",
        "dialogs",
        "manual_verdict IS NULL OR manual_verdict IN ('allow', 'deny')",
        schema=SCHEMA,
    )
    op.create_table(
        "verdicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("tail_event_id", sa.String(64), nullable=False),
        sa.Column("tail_fingerprint", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.String(8), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_wb_support_bot_verdicts_status"),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('open', 'closing', 'unclear')", name="ck_wb_support_bot_verdicts_verdict"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_wb_support_bot_verdicts_tail",
        "verdicts",
        ["seller_id", "chat_id", "tail_fingerprint", "prompt_version"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index("ix_wb_support_bot_verdicts_status", "verdicts", ["status", "requested_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("verdicts", schema=SCHEMA)
    op.drop_constraint("ck_wb_support_bot_dialogs_manual", "dialogs", schema=SCHEMA, type_="check")
    for column in ("manual_fingerprint", "manual_verdict", "tail_fingerprint", "tail_event_id"):
        op.drop_column("dialogs", column, schema=SCHEMA)
