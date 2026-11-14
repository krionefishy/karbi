"""Read-path indexes for the ma-bridge export cursor."""

from alembic import op

revision = "wc023"
down_revision = "wc022"
branch_labels = None
depends_on = None

SCHEMA = "wb_core"
INDEXES = (
    ("ix_wb_core_sellers_export", "sellers", ["updated_at", "id"]),
    ("ix_wb_core_articles_export", "articles", ["updated_at", "id"]),
    (
        "ix_wb_core_sales_report_rows_export",
        "sales_report_rows",
        ["collected_at", "seller_id", "rrd_id"],
    ),
    (
        "ix_wb_core_advert_campaigns_export",
        "advert_campaigns",
        ["collected_at", "seller_id", "advert_id"],
    ),
    (
        "ix_wb_core_advert_nm_stats_export",
        "advert_nm_stats",
        ["collected_at", "seller_id", "advert_id", "day", "nm_id"],
    ),
    ("ix_wb_core_stock_facts_export", "stock_facts", ["collected_at", "seller_id", "article"]),
    (
        "ix_wb_core_warehouse_remains_export",
        "warehouse_remains",
        ["collected_at", "seller_id", "article", "warehouse_name", "barcode"],
    ),
    (
        "ix_wb_core_fbs_warehouse_stocks_export",
        "fbs_warehouse_stocks",
        ["collected_at", "seller_id", "article", "warehouse_id"],
    ),
    ("ix_wb_core_review_facts_export", "review_facts", ["collected_at", "seller_id", "article"]),
)


def upgrade() -> None:
    # Source collectors keep writing while a potentially large history index is built.
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns, schema=SCHEMA, postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _ in reversed(INDEXES):
            op.drop_index(name, table_name=table, schema=SCHEMA, postgresql_concurrently=True)
