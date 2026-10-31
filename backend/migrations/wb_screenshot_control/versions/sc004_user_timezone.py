"""Пояс менеджера: таблица `user_settings` и пояс, в котором прочитано время каждого скрина.

`submissions.review_tz` у старых скринов остаётся NULL: их время прочитано в поясе модуля (Москва)
и не пересчитывается.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "sc004"
down_revision = "sc003"
branch_labels = None
depends_on = None

SCHEMA = "wb_screenshot_control"


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.add_column("submissions", sa.Column("review_tz", sa.String(64), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("submissions", "review_tz", schema=SCHEMA)
    op.drop_table("user_settings", schema=SCHEMA)
