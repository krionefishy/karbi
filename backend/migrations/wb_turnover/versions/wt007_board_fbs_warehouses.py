import sqlalchemy as sa
from alembic import op

revision = "wt007"
down_revision = "wt006"
branch_labels = None
depends_on = None

# Склады, по которым таблица оборачиваемости считает «Остатки ФБС» с самого начала.
# Названия во всех кабинетах одни, а id разные — поэтому ключ именно название.
DEFAULT_WAREHOUSES = (
    "1. Москва Домодедово",
    "2. Тагил ( ЕКБ)",
    "3. Новосибирск",
    "4. Питер наш",
    "5. Казань наша",
    "6. Краснодар",
)


def upgrade() -> None:
    table = op.create_table(
        "board_fbs_warehouses",
        sa.Column("name_key", sa.String(255), primary_key=True),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="wb_turnover",
    )
    op.bulk_insert(
        table,
        [{"name_key": " ".join(name.lower().split()), "label": " ".join(name.split())} for name in DEFAULT_WAREHOUSES],
    )


def downgrade() -> None:
    op.drop_table("board_fbs_warehouses", schema="wb_turnover")
