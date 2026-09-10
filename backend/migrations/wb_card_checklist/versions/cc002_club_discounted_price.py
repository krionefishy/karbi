"""Цена для подписчиков WB Клуба — для пункта «Скидка WB Клуба».

Колонка nullable: у цен, собранных раньше, её нет, а ближайший суточный сбор
перепишет цены селлера целиком.
"""

import sqlalchemy as sa
from alembic import op

revision = "cc002"
down_revision = "cc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "price_facts",
        sa.Column("club_discounted_price", sa.Numeric(12, 2), nullable=True),
        schema="wb_card_checklist",
    )


def downgrade() -> None:
    op.drop_column("price_facts", "club_discounted_price", schema="wb_card_checklist")
