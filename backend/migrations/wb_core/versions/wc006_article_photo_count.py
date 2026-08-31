"""Сколько фото в карточке: по этому числу оборачиваемость отбирает товары.

Колонка nullable намеренно. NULL — «каталогом ещё не читали», и такой товар
из расчёта не исключается: молча погасить уведомления по всему ассортименту до
первой синхронизации хуже, чем лишний раз посчитать карточку с двумя фото.
Значения проставляет ближайший синк каталога селлера.
"""

import sqlalchemy as sa
from alembic import op

revision = "wc006"
down_revision = "wc005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("photo_count", sa.Integer(), nullable=True), schema="wb_core")


def downgrade() -> None:
    op.drop_column("articles", "photo_count", schema="wb_core")
