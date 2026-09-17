"""Сводка строки с заданием хранится на строке.

Склад продавца и поставка считались при каждом запросе по зеркалу заданий: у
крупного кабинета это десятки тысяч ключей на страницу и выгрузку. Теперь они
считаются один раз при сборе, а несведённые строки досводятся воркером.
"""

import sqlalchemy as sa
from alembic import op

revision = "fp004"
down_revision = "fp003"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_penalties"


def upgrade() -> None:
    op.add_column(
        "report_rows",
        sa.Column("trace", sa.String(16), nullable=False, server_default="no_order"),
        schema=SCHEMA,
    )
    op.add_column("report_rows", sa.Column("warehouse_id", sa.BigInteger(), nullable=True), schema=SCHEMA)
    op.add_column("report_rows", sa.Column("warehouse_name", sa.String(255), nullable=True), schema=SCHEMA)
    op.add_column(
        "report_rows", sa.Column("order_created_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column("report_rows", sa.Column("supply_id", sa.String(32), nullable=True), schema=SCHEMA)
    op.add_column(
        "report_rows", sa.Column("supply_created_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA
    )
    op.add_column("report_rows", sa.Column("supply_scan_dt", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column("report_rows", sa.Column("destination_office_name", sa.String(255), nullable=True), schema=SCHEMA)
    op.add_column("report_rows", sa.Column("traced_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.create_index(
        "ix_wb_fbs_penalties_rows_warehouse", "report_rows", ["seller_id", "warehouse_id", "rr_dt"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_wb_fbs_penalties_rows_warehouse", table_name="report_rows", schema=SCHEMA)
    for column in (
        "traced_at",
        "destination_office_name",
        "supply_scan_dt",
        "supply_created_at",
        "supply_id",
        "order_created_at",
        "warehouse_name",
        "warehouse_id",
        "trace",
    ):
        op.drop_column("report_rows", column, schema=SCHEMA)
