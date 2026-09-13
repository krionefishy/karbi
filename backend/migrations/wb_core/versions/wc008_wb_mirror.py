"""Зеркало WB: остатки и отзывы по карточкам рядом с каталогом.

Данные, которые нужны нескольким автоматизациям, собираются один раз
воркером wb_core по всем активным селлерам, а не каждой автоматизацией для
своих подключённых. Критерий, что здесь лежит: копия ответа WB по селлеру и
карточке, не наш расчёт.

`mirror_state` — отметки сбора по виду: у каталога, остатков и отзывов своё
расписание и свои ошибки. Ссылок на `sellers` нет, как и у `tracked_sellers`
автоматизаций: удаление селлера чистит эти таблицы явно.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc008"
down_revision = "wc007"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def upgrade() -> None:
    op.create_table(
        "mirror_state",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.CheckConstraint("kind IN ('catalog', 'stocks', 'reviews')", name="ck_wb_core_mirror_state_kind"),
        schema=SCHEMA,
    )
    op.create_table(
        "stock_facts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("fbo_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fbo_quantity_full", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fbs_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "fbs_warehouse_stocks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "review_facts",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("count_rating_1", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_rating_2", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_rating_3", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_rating_4", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_rating_5", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("count_with_photo", sa.Integer(), nullable=True),
        sa.Column("count_with_video", sa.Integer(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in ("review_facts", "fbs_warehouse_stocks", "stock_facts", "mirror_state"):
        op.drop_table(table, schema=SCHEMA)
