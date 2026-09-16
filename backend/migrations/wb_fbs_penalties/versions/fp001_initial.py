"""Штрафы FBS: подключённые кабинеты, строки фин. отчёта с удержаниями, запросы «Обновить».

Строки отчёта хранятся только с удержаниями: продажи без штрафов этой
автоматизации не нужны. Ключ — `rrd_id` WB, по нему повторный сбор того же
периода ничего не задваивает.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fp001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_penalties"
MONEY = sa.Numeric(14, 2)


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_error", sa.String(), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "report_rows",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("rrd_id", sa.BigInteger(), primary_key=True),
        sa.Column("realizationreport_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("create_dt", sa.Date(), nullable=True),
        sa.Column("srid", sa.String(64), nullable=False, server_default=""),
        sa.Column("assembly_id", sa.BigInteger(), nullable=True),
        sa.Column("sticker_id", sa.BigInteger(), nullable=True),
        sa.Column("order_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sale_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rr_dt", sa.Date(), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sa_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("barcode", sa.String(64), nullable=False, server_default=""),
        sa.Column("ts_name", sa.String(64), nullable=False, server_default=""),
        sa.Column("bonus_type_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("supplier_oper_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("delivery_method", sa.String(64), nullable=False, server_default=""),
        sa.Column("office_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("penalty", MONEY, nullable=False, server_default="0"),
        sa.Column("deduction", MONEY, nullable=False, server_default="0"),
        sa.Column("rebill_logistic_cost", MONEY, nullable=False, server_default="0"),
        sa.Column("storage_fee", MONEY, nullable=False, server_default="0"),
        sa.Column("additional_payment", MONEY, nullable=False, server_default="0"),
        sa.Column("acceptance", MONEY, nullable=False, server_default="0"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    for name, columns in (
        ("ix_wb_fbs_penalties_rows_period", ["seller_id", "rr_dt"]),
        ("ix_wb_fbs_penalties_rows_sticker", ["seller_id", "sticker_id"]),
        ("ix_wb_fbs_penalties_rows_assembly", ["seller_id", "assembly_id"]),
        ("ix_wb_fbs_penalties_rows_srid", ["seller_id", "srid"]),
    ):
        op.create_index(name, "report_rows", columns, schema=SCHEMA)
    op.create_table(
        "refresh_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'success', 'error')", name="ck_wb_fbs_penalties_refresh_status"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wb_fbs_penalties_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema=SCHEMA
    )
    op.create_index(
        "uq_wb_fbs_penalties_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    for table in ("refresh_requests", "report_rows", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
