"""Конвейер: совпадения решения у сабмишена и индекс для истечения исхода."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "sc002"
down_revision = "sc001"
branch_labels = None
depends_on = None

SCHEMA = "wb_screenshot_control"


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("matches", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_sc_sub_no_outcome",
        "submissions",
        ["received_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("outcome IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sc_sub_no_outcome", table_name="submissions", schema=SCHEMA)
    op.drop_column("submissions", "matches", schema=SCHEMA)
