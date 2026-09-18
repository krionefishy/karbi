"""Зеркало чатов с покупателями: лента событий и курсор чтения.

Нужно отчёту по диалогам после отзыва с низкой оценкой: WB сам открывает чат
автосообщением, следом уходит наше, и считается, кто из покупателей ответил.
По правилу зеркала лежит в `wb_core` — это копия ленты WB, а не расчёт.

Лента одна на кабинет и только дописывается, поэтому читается курсором, а
курсор хранится отдельно от отметки сбора: историю догоняем порциями, и
«дочитали до конца» — не то же самое, что «сбор прошёл без ошибки».

Частичный индекс — под единственный частый вопрос отчёта: автосообщения WB
за период. Их доли процента от всех событий.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc010"
down_revision = "wc009"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies')"
KINDS_AFTER = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats')"


def _seller_column() -> sa.Column:
    return sa.Column(
        "seller_id", UUID(as_uuid=True), sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"), primary_key=True
    )


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_AFTER, schema=SCHEMA)
    op.create_table(
        "chat_events",
        _seller_column(),
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("sender", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default=""),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_new_chat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_prompt", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("rid", sa.String(64), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_core_chat_events_chat", "chat_events", ["seller_id", "chat_id", "added_at"], schema=SCHEMA)
    op.create_index(
        "ix_wb_core_chat_events_prompts",
        "chat_events",
        ["seller_id", "added_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("review_prompt"),
    )
    op.create_table(
        "chat_cursors",
        _seller_column(),
        sa.Column("next", sa.BigInteger(), nullable=False),
        sa.Column("tail_reached_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("chat_cursors", schema=SCHEMA)
    op.drop_table("chat_events", schema=SCHEMA)
    op.execute("DELETE FROM wb_core.mirror_state WHERE kind = 'chats'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
