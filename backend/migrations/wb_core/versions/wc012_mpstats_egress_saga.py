"""Сага доставки токена MPStats: свой статус рядом с WB и Ozon.

Токена здесь, как и ключей маркетплейсов, нет — он живёт на шлюзе. Здесь
только исход доставки, чтобы админка могла его показать.

MPStats — не маркетплейс, а аналитика по ним, но токен у каждого селлера свой
и уходит в MPStats с закреплённого за селлером адреса, поэтому учётка устроена
так же: отдельная пара колонок, общие адрес и версия события.

Статусы существующих селлеров добирает сверка со шлюзом
(backend/commands/sync_egress_status.py) после выкатки.
"""

import sqlalchemy as sa
from alembic import op

revision = "wc012"
down_revision = "wc011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sellers",
        sa.Column("mpstats_egress_status", sa.String(16), nullable=False, server_default="undelivered"),
        schema="wb_core",
    )
    op.add_column("sellers", sa.Column("mpstats_egress_error", sa.String(), nullable=True), schema="wb_core")


def downgrade() -> None:
    op.drop_column("sellers", "mpstats_egress_error", schema="wb_core")
    op.drop_column("sellers", "mpstats_egress_status", schema="wb_core")
