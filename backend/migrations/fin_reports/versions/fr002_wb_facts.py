"""Сложенные строки отчётов реализации WB — то, из чего страница и выгрузка читают отчёт.

Раньше ОПиУ складывался из зеркала при каждом открытии страницы: миллионы
строк на запрос. Отчёт WB неизменяем, поэтому воркер складывает его один
раз — по артикулу, размеру, месяцу операции и виду операции. Версия правила
сложения хранится на отчёте: сменилась — отчёт пересобирается.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fr002"
down_revision = "fr001"
branch_labels = None
depends_on = None

SCHEMA = "fin_reports"
MONEY = sa.Numeric(16, 2)


def upgrade() -> None:
    op.create_table(
        "wb_reports",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", sa.BigInteger(), primary_key=True),
        sa.Column("report_type", sa.Integer(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_fin_reports_wb_reports_dates", "wb_reports", ["seller_id", "date_from"], schema=SCHEMA)
    op.create_table(
        "wb_facts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("month", sa.Date(), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("vendor_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("tech_size", sa.Text(), nullable=False, server_default=""),
        sa.Column("doc_type_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("seller_oper_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("bonus_type_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("srv_dbs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("return_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gross", MONEY, nullable=False, server_default="0"),
        sa.Column("retail_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("for_pay", MONEY, nullable=False, server_default="0"),
        sa.Column("delivery_service", MONEY, nullable=False, server_default="0"),
        sa.Column("rebill_logistic_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("penalty", MONEY, nullable=False, server_default="0"),
        sa.Column("additional_payment", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_storage", MONEY, nullable=False, server_default="0"),
        sa.Column("deduction", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_acceptance", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_discount", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_commission_change", MONEY, nullable=False, server_default="0"),
        sa.Column("acquiring_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("ppvz_reward", MONEY, nullable=False, server_default="0"),
        sa.Column("vw", MONEY, nullable=False, server_default="0"),
        sa.Column("vw_nds", MONEY, nullable=False, server_default="0"),
        sa.Column("ppvz_sales_commission", MONEY, nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_index("ix_fin_reports_wb_facts_report", "wb_facts", ["seller_id", "report_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("wb_facts", schema=SCHEMA)
    op.drop_table("wb_reports", schema=SCHEMA)
