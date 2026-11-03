"""Документы карточки: что видит блок `documents` на момент сбора.

NULL у существующих строк — «документы не читали»: их заполнит следующий суточный сбор.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "cc003"
down_revision = "cc002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("card_facts", sa.Column("documents", JSONB(), nullable=True), schema="wb_card_checklist")


def downgrade() -> None:
    op.drop_column("card_facts", "documents", schema="wb_card_checklist")
