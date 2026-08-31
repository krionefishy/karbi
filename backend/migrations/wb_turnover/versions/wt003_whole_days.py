"""Дни оборачиваемости — целые, всегда вниз.

История тоже приводится к целым: дайджест показывает «вчера было столько-то»
из вчерашней строки, и одна дробная цифра рядом с целой читалась бы как ошибка.
Округление вниз, а не арифметическое: 0.8 дня запаса — это ноль дней в руках.
"""

import sqlalchemy as sa
from alembic import op

revision = "wt003"
down_revision = "wt002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("days_of_cover", "turnover_days"):
        op.alter_column(
            "turnover_daily",
            column,
            type_=sa.Integer(),
            existing_type=sa.Numeric(10, 2),
            existing_nullable=True,
            postgresql_using=f"floor({column})::integer",
            schema="wb_turnover",
        )


def downgrade() -> None:
    for column in ("days_of_cover", "turnover_days"):
        op.alter_column(
            "turnover_daily",
            column,
            type_=sa.Numeric(10, 2),
            existing_type=sa.Integer(),
            existing_nullable=True,
            schema="wb_turnover",
        )
