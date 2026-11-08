import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "sb009"
down_revision = "sb008"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"


def upgrade() -> None:
    op.add_column("dialogs", sa.Column("manual_until", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.create_table(
        "product_switches",
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("seller_id", "nm_id"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("product_switches", schema=SCHEMA)
    op.drop_column("dialogs", "manual_until", schema=SCHEMA)
