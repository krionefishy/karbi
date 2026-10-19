import sqlalchemy as sa
from alembic import op

revision = "p002"
down_revision = "p001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin", schema="platform")
