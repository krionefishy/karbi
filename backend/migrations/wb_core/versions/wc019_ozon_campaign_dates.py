"""Даты кампаний Ozon — статистику просят только за окна, которые кампания застала.

Первая выкладка wc018 ушла без этих колонок; у кого она уже применена, колонки
добавляются здесь, у остальных их создаёт wc018.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "wc019"
down_revision = "wc018"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
COLUMNS = ("created_at", "from_date", "to_date")


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("ozon_advert_campaigns", schema=SCHEMA)}
    for name in COLUMNS:
        if name not in existing:
            op.add_column("ozon_advert_campaigns", sa.Column(name, sa.Date(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("ozon_advert_campaigns", name, schema=SCHEMA)
