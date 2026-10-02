"""Зеркало отчётов реализации WB: шапки с итогами и строки детализации.

Деньги селлера считают по недельным отчётам реализации, а строки этих
отчётов до сих пор читал только модуль штрафов — и хранил из них одни
удержания. По правилу зеркала ответ WB лежит в `wb_core` целиком: шапка с
итогами WB, по которым сверяется полнота, и строки с полями, названными как
у WB. Страницы детализации пишутся по одной с курсором в шапке.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wc013"
down_revision = "wc012"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
KINDS_BEFORE = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains')"
KINDS_AFTER = "kind IN ('catalog', 'stocks', 'reviews', 'orders', 'supplies', 'chats', 'remains', 'sales_reports')"
MONEY = sa.Numeric(14, 2)
RATIO = sa.Numeric(14, 4)


def upgrade() -> None:
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_AFTER, schema=SCHEMA)
    op.create_table(
        "sales_reports",
        sa.Column(
            "seller_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("report_id", sa.BigInteger(), primary_key=True),
        sa.Column("report_type", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("create_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default=""),
        sa.Column("seller_finance_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("retail_amount_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("for_pay_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("delivery_service_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_storage_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_acceptance_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("deduction_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("penalty_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("additional_payment_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_amount_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_discount_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_commission_change_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("bank_payment_sum", MONEY, nullable=False, server_default="0"),
        sa.Column("rows_cursor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rows_loaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("period IN ('weekly', 'daily')", name="ck_wb_core_sales_reports_period"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_core_sales_reports_period", "sales_reports", ["seller_id", "period", "date_from"], schema=SCHEMA
    )
    op.create_table(
        "sales_report_rows",
        sa.Column(
            "seller_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("rrd_id", sa.BigInteger(), primary_key=True),
        sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("gi_id", sa.BigInteger(), nullable=True),
        sa.Column("doc_type_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("seller_oper_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("bonus_type_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("srid", sa.Text(), nullable=False, server_default=""),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("shk_id", sa.BigInteger(), nullable=True),
        sa.Column("sticker_id", sa.BigInteger(), nullable=True),
        sa.Column("order_uid", sa.Text(), nullable=False, server_default=""),
        sa.Column("trbx_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("order_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sale_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rr_date", sa.Date(), nullable=True),
        sa.Column("fix_tariff_date_from", sa.Date(), nullable=True),
        sa.Column("fix_tariff_date_to", sa.Date(), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("vendor_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("brand_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("tech_size", sa.Text(), nullable=False, server_default=""),
        sa.Column("sku", sa.Text(), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retail_price", MONEY, nullable=False, server_default="0"),
        sa.Column("retail_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("retail_price_withdisc", MONEY, nullable=False, server_default="0"),
        sa.Column("sale_percent", RATIO, nullable=False, server_default="0"),
        sa.Column("commission_percent", RATIO, nullable=False, server_default="0"),
        sa.Column("spp", RATIO, nullable=False, server_default="0"),
        sa.Column("product_discount_for_report", RATIO, nullable=False, server_default="0"),
        sa.Column("seller_promo", RATIO, nullable=False, server_default="0"),
        sa.Column("seller_promo_id", sa.BigInteger(), nullable=True),
        sa.Column("seller_promo_discount", RATIO, nullable=False, server_default="0"),
        sa.Column("kvw_base", RATIO, nullable=False, server_default="0"),
        sa.Column("kvw", RATIO, nullable=False, server_default="0"),
        sa.Column("sup_rating_up", RATIO, nullable=False, server_default="0"),
        sa.Column("is_kgvp_v2", RATIO, nullable=False, server_default="0"),
        sa.Column("dlv_prc", RATIO, nullable=False, server_default="0"),
        sa.Column("ppvz_sales_commission", MONEY, nullable=False, server_default="0"),
        sa.Column("for_pay", MONEY, nullable=False, server_default="0"),
        sa.Column("ppvz_reward", MONEY, nullable=False, server_default="0"),
        sa.Column("acquiring_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("acquiring_percent", RATIO, nullable=False, server_default="0"),
        sa.Column("acquiring_bank", sa.Text(), nullable=False, server_default=""),
        sa.Column("payment_processing", sa.Text(), nullable=False, server_default=""),
        sa.Column("vw", MONEY, nullable=False, server_default="0"),
        sa.Column("vw_nds", MONEY, nullable=False, server_default="0"),
        sa.Column("delivery_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("return_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_service", MONEY, nullable=False, server_default="0"),
        sa.Column("rebill_logistic_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("rebill_logistic_org", sa.Text(), nullable=False, server_default=""),
        sa.Column("penalty", MONEY, nullable=False, server_default="0"),
        sa.Column("additional_payment", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_storage", MONEY, nullable=False, server_default="0"),
        sa.Column("deduction", MONEY, nullable=False, server_default="0"),
        sa.Column("paid_acceptance", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_discount", MONEY, nullable=False, server_default="0"),
        sa.Column("cashback_commission_change", MONEY, nullable=False, server_default="0"),
        sa.Column("installment_cofinancing_amount", MONEY, nullable=False, server_default="0"),
        sa.Column("wibes_discount_percent", RATIO, nullable=False, server_default="0"),
        sa.Column("loyalty_id", sa.BigInteger(), nullable=True),
        sa.Column("loyalty_discount", RATIO, nullable=False, server_default="0"),
        sa.Column("warehouse_logistics_coeff", RATIO, nullable=False, server_default="0"),
        sa.Column("payment_schedule", sa.Text(), nullable=False, server_default=""),
        sa.Column("office_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("ppvz_office_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("ppvz_office_id", sa.BigInteger(), nullable=True),
        sa.Column("delivery_method", sa.Text(), nullable=False, server_default=""),
        sa.Column("srv_dbs", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_b2b", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("country", sa.Text(), nullable=False, server_default=""),
        sa.Column("gi_box_type_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("declaration_number", sa.Text(), nullable=False, server_default=""),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_core_sales_report_rows_report", "sales_report_rows", ["seller_id", "report_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_wb_core_sales_report_rows_nm", "sales_report_rows", ["seller_id", "nm_id", "rr_date"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_table("sales_report_rows", schema=SCHEMA)
    op.drop_table("sales_reports", schema=SCHEMA)
    op.execute(f"DELETE FROM {SCHEMA}.mirror_state WHERE kind = 'sales_reports'")
    op.drop_constraint("ck_wb_core_mirror_state_kind", "mirror_state", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_wb_core_mirror_state_kind", "mirror_state", KINDS_BEFORE, schema=SCHEMA)
