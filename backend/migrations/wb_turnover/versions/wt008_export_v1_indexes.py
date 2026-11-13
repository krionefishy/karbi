"""Read-path index for the ma-bridge orders cursor."""

from alembic import op

revision = "wt008"
down_revision = "wt007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_wb_turnover_orders_export",
            "orders",
            ["collected_at", "srid"],
            schema="wb_turnover",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_wb_turnover_orders_export",
            table_name="orders",
            schema="wb_turnover",
            postgresql_concurrently=True,
        )
