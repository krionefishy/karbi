"""Чаты после отзыва: подключённые кабинеты.

Больше таблиц нет нарочно: события чатов лежат в зеркале `wb_core`, а отчёт
считается из них при чтении — окно ответа и правила можно менять, не
пересобирая историю.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "rc001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_review_chats"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("tracked_sellers", schema=SCHEMA)
