"""Рейтинг карточки с витрины WB (воронка продаж) рядом со счётчиками отзывов.

NULL — WB карточку не вернул (болванка без показов) или рейтинг ещё не собирали.
"""

import sqlalchemy as sa
from alembic import op

revision = "wc021"
down_revision = "wc020"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def upgrade() -> None:
    op.add_column("review_facts", sa.Column("wb_rating", sa.Numeric(3, 2), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("review_facts", "wb_rating", schema=SCHEMA)
