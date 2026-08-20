import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wt001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("orders_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warehouses_synced_at", sa.DateTime(timezone=True), nullable=True),
        schema="wb_turnover",
    )

    op.create_table(
        "seller_warehouses",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), server_default="", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="wb_turnover",
    )

    op.create_table(
        "stock_snapshots",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("snapshot_date", sa.Date(), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("delivery_model", sa.String(8), primary_key=True),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quantity_full", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_way_to_client", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_way_from_client", sa.Integer(), server_default="0", nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("delivery_model IN ('fbo', 'fbs')", name="ck_wb_turnover_snapshot_model"),
        schema="wb_turnover",
    )
    op.create_index(
        "ix_wb_turnover_snapshots_window", "stock_snapshots", ["seller_id", "snapshot_date"], schema="wb_turnover"
    )

    op.create_table(
        "orders",
        sa.Column("srid", sa.String(128), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("article", sa.String(255), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("last_change_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_cancel", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("warehouse_type", sa.String(64), server_default="", nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="wb_turnover",
    )
    op.create_index("ix_wb_turnover_orders_window", "orders", ["seller_id", "order_date"], schema="wb_turnover")
    op.create_index(
        "ix_wb_turnover_orders_article", "orders", ["seller_id", "article", "order_date"], schema="wb_turnover"
    )

    op.create_table(
        "turnover_daily",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("stock_fbo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stock_fbs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stock_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_stock", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("orders_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cancelled_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("avg_daily_orders", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("days_of_cover", sa.Numeric(10, 2), nullable=True),
        sa.Column("turnover_days", sa.Numeric(10, 2), nullable=True),
        sa.Column("stock_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sales_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(24), server_default="ok", nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ok', 'no_stock', 'no_sales', 'insufficient_data')", name="ck_wb_turnover_daily_status"
        ),
        schema="wb_turnover",
    )
    op.create_index(
        "ix_wb_turnover_daily_alerts", "turnover_daily", ["date", "status", "days_of_cover"], schema="wb_turnover"
    )

    op.create_table(
        "collection_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.Integer(), server_default="0", nullable=False),
        sa.Column("trigger", sa.String(16), server_default="scheduled", nullable=False),
        sa.Column("status", sa.String(16), server_default="running", nullable=False),
        sa.Column("sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_sellers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('stocks', 'orders', 'calculation', 'digest')", name="ck_wb_turnover_runs_kind"),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_success', 'error')", name="ck_wb_turnover_runs_status"
        ),
        sa.UniqueConstraint("kind", "run_date", "slot", "trigger", name="uq_wb_turnover_run_slot"),
        schema="wb_turnover",
    )
    op.create_index("ix_wb_turnover_runs_recent", "collection_runs", ["started_at"], schema="wb_turnover")

    op.create_table(
        "notification_log",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("articles_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema="wb_turnover",
    )


def downgrade() -> None:
    op.drop_table("notification_log", schema="wb_turnover")
    op.drop_table("collection_runs", schema="wb_turnover")
    op.drop_table("turnover_daily", schema="wb_turnover")
    op.drop_table("orders", schema="wb_turnover")
    op.drop_table("stock_snapshots", schema="wb_turnover")
    op.drop_table("seller_warehouses", schema="wb_turnover")
    op.drop_table("tracked_sellers", schema="wb_turnover")
