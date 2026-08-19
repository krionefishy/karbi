import sqlalchemy as sa
from alembic import op

revision = "wc004"
down_revision = "wc003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deleting a seller used to be the only way to take him out of an automation,
    # which destroyed the review history with him. Archiving keeps the history and
    # is reversible; the flag alone could not say when he left.
    op.add_column("sellers", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True), schema="wb_core")
    op.execute("UPDATE wb_core.sellers SET archived_at = now() WHERE NOT is_active")
    op.drop_index("ix_wb_core_sellers_active", table_name="sellers", schema="wb_core")
    op.drop_column("sellers", "is_active", schema="wb_core")
    op.create_index("ix_wb_core_sellers_archived", "sellers", ["archived_at"], schema="wb_core")


def downgrade() -> None:
    op.add_column(
        "sellers", sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False), schema="wb_core"
    )
    op.execute("UPDATE wb_core.sellers SET is_active = (archived_at IS NULL)")
    op.drop_index("ix_wb_core_sellers_archived", table_name="sellers", schema="wb_core")
    op.drop_column("sellers", "archived_at", schema="wb_core")
    op.create_index("ix_wb_core_sellers_active", "sellers", ["is_active"], schema="wb_core")
