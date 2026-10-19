import sqlalchemy as sa
from alembic import op

revision = "wc005"
down_revision = "wc004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sellers",
        sa.Column("egress_status", sa.String(16), nullable=False, server_default="undelivered"),
        schema="wb_core",
    )
    op.add_column("sellers", sa.Column("egress_error", sa.String(), nullable=True), schema="wb_core")
    op.add_column("sellers", sa.Column("egress_ip", sa.String(45), nullable=True), schema="wb_core")
    op.add_column(
        "sellers", sa.Column("egress_version", sa.BigInteger(), nullable=False, server_default="0"), schema="wb_core"
    )
    op.drop_table("credentials", schema="wb_core")


def downgrade() -> None:
    op.create_table(
        "credentials",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "seller_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wb_core.sellers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("encrypted_api_key", sa.String(), nullable=False),
        sa.Column("key_fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="wb_core",
    )
    op.drop_column("sellers", "egress_version", schema="wb_core")
    op.drop_column("sellers", "egress_ip", schema="wb_core")
    op.drop_column("sellers", "egress_error", schema="wb_core")
    op.drop_column("sellers", "egress_status", schema="wb_core")
