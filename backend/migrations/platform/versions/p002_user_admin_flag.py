import sqlalchemy as sa
from alembic import op

revision = "p002"
down_revision = "p001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The admin section lives in the same API, and the flag is read from the
    # database on every admin request instead of riding in the access token:
    # revoking rights then takes effect at once rather than when the token
    # expires a day later.
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin", schema="platform")
