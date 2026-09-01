"""Сага доставки учётки Ozon: свой статус рядом с WB.

Ключей Ozon здесь, как и ключей WB, нет — они живут на шлюзе. Здесь только
исход доставки, чтобы админка могла его показать.

Отдельная пара колонок, а не общая: учётки маркетплейсов независимы, и у
одного селлера ключ WB может быть проверен, пока ключ Ozon отозван. Прежние
`egress_status` и `egress_error` остаются за Wildberries и не переименованы —
их читают фронтенд и сверка, а переименование колонки ради симметрии стоило бы
дороже, чем комментарий.

Адрес (`egress_ip`) и версия события (`egress_version`) общие для маркетплейсов:
адрес у селлера один на оба, а версия — просто монотонный счётчик правок.

Статусы существующих селлеров добирает сверка со шлюзом
(backend/commands/sync_egress_status.py) после выкатки.
"""

import sqlalchemy as sa
from alembic import op

revision = "wc007"
down_revision = "wc006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sellers",
        sa.Column("ozon_egress_status", sa.String(16), nullable=False, server_default="undelivered"),
        schema="wb_core",
    )
    op.add_column("sellers", sa.Column("ozon_egress_error", sa.String(), nullable=True), schema="wb_core")


def downgrade() -> None:
    op.drop_column("sellers", "ozon_egress_error", schema="wb_core")
    op.drop_column("sellers", "ozon_egress_status", schema="wb_core")
