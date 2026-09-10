"""Сколько отзывов артикула пришли с фото и с видео.

Колонки nullable намеренно: у срезов, снятых раньше, этих чисел нет, и ноль
соврал бы чек-листу карточки, что фото-отзывов не было. Значения появятся с
ближайшего ночного прогона отзывов.
"""

import sqlalchemy as sa
from alembic import op

revision = "wr006"
down_revision = "wr005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_review_counts", sa.Column("count_with_photo", sa.Integer(), nullable=True), schema="wb_reviews"
    )
    op.add_column(
        "daily_review_counts", sa.Column("count_with_video", sa.Integer(), nullable=True), schema="wb_reviews"
    )


def downgrade() -> None:
    op.drop_column("daily_review_counts", "count_with_video", schema="wb_reviews")
    op.drop_column("daily_review_counts", "count_with_photo", schema="wb_reviews")
