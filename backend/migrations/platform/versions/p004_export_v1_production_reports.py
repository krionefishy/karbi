import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p004"
down_revision = "p003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "export_v1_production_reports",
        sa.Column("dataset", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rows", sa.BigInteger(), nullable=False),
        sa.Column("sent_rows", sa.BigInteger()),
        sa.Column("real_sellers", sa.BigInteger(), nullable=False),
        sa.Column("mock_sellers", sa.BigInteger(), nullable=False),
        sa.Column("mock_rows", sa.BigInteger(), nullable=False),
        sa.Column("rejected_rows", sa.BigInteger(), nullable=False),
        sa.Column("unmapped_sellers", sa.BigInteger(), nullable=False),
        sa.Column("backlog_rows", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "dataset IN ('sellers', 'products', 'orders', 'sales_rows', 'prices', "
            "'ad_campaigns', 'ad_stats_daily', 'stocks', 'ratings')",
            name="ck_export_v1_report_dataset",
        ),
        sa.CheckConstraint("channel = 'wb'", name="ck_export_v1_report_channel"),
        sa.CheckConstraint(
            "(source = 'ma_bridge' AND stage = 'accepted' AND sent_rows IS NOT NULL) OR "
            "(source = 'mp_sync' AND stage = 'written' AND sent_rows IS NULL)",
            name="ck_export_v1_report_source_stage",
        ),
        sa.CheckConstraint(
            "rows >= 0 AND (sent_rows IS NULL OR sent_rows >= 0) AND real_sellers >= 0 "
            "AND mock_sellers >= 0 AND mock_rows >= 0 AND rejected_rows >= 0 "
            "AND unmapped_sellers >= 0 AND backlog_rows >= 0",
            name="ck_export_v1_report_non_negative",
        ),
        sa.PrimaryKeyConstraint("dataset", "channel", "source"),
        schema="platform",
    )
    op.create_index(
        "ix_export_v1_production_reports_generation",
        "export_v1_production_reports",
        ["channel", "source", "observed_at"],
        schema="platform",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_export_v1_production_reports_generation",
        table_name="export_v1_production_reports",
        schema="platform",
    )
    op.drop_table("export_v1_production_reports", schema="platform")
