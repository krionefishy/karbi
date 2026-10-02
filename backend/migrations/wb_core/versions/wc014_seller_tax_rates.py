"""Ставка налога селлера с датой начала действия.

Ставка — свойство юрлица: она одна на WB и Ozon и нужна любому расчёту
прибыли, поэтому лежит в реестре, а не в автоматизации. Версии не
переписываются: ставки меняются с нового года, и прошлые периоды остаются со
своей.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc014"
down_revision = "wc013"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"


def upgrade() -> None:
    op.create_table(
        "seller_tax_rates",
        sa.Column(
            "seller_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("effective_from", sa.Date(), primary_key=True),
        sa.Column("rate", sa.Numeric(5, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("rate >= 0 AND rate <= 100", name="ck_wb_core_seller_tax_rates_rate"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("seller_tax_rates", schema=SCHEMA)
