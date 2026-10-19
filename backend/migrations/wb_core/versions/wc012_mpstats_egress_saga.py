import sqlalchemy as sa
from alembic import op

revision = "wc012"
down_revision = "wc011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sellers",
        sa.Column("mpstats_egress_status", sa.String(16), nullable=False, server_default="undelivered"),
        schema="wb_core",
    )
    op.add_column("sellers", sa.Column("mpstats_egress_error", sa.String(), nullable=True), schema="wb_core")


def downgrade() -> None:
    op.drop_column("sellers", "mpstats_egress_error", schema="wb_core")
    op.drop_column("sellers", "mpstats_egress_status", schema="wb_core")
