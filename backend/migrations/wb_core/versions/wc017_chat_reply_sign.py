"""Подпись чата в событиях: нужна, чтобы отвечать покупателю.

Лента чатов отдаёт `replySign` в каждом событии; раньше он отбрасывался, потому
что сервис только читал чаты. Бот поддержки отвечает в чат методом
`POST /api/v1/seller/message`, которому нужна подпись чата. У уже собранных
событий подписи нет: она появится у новых.
"""

import sqlalchemy as sa
from alembic import op

revision = "wc017"
down_revision = "wc016"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def upgrade() -> None:
    op.add_column("chat_events", sa.Column("reply_sign", sa.String(255), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("chat_events", "reply_sign", schema=SCHEMA)
