"""Пять свежих оценок на карточку — для пункта чек-листа «негатив в последних отзывах».

NULL у существующих строк — «свежие не запоминали»: их заполнит следующий сбор отзывов.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "wc020"
down_revision = "wc019"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def upgrade() -> None:
    op.add_column("review_facts", sa.Column("latest_ratings", JSONB(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    op.drop_column("review_facts", "latest_ratings", schema=SCHEMA)
