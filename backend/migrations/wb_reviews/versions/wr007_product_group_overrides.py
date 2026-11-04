"""Ручные правки групп болванок в мониторинге отзывов поверх склеек WB."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "wr007"
down_revision = "wr006"
branch_labels = None
depends_on = None

SCHEMA = "wb_reviews"


def upgrade() -> None:
    op.create_table(
        "product_group_overrides",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("article", sa.String(255), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("main_article", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("kind IN ('main', 'member', 'solo')", name="ck_wb_reviews_group_overrides_kind"),
        sa.CheckConstraint(
            "(kind = 'member' AND main_article IS NOT NULL) OR (kind = 'solo' AND main_article IS NULL) "
            "OR kind = 'main'",
            name="ck_wb_reviews_group_overrides_target",
        ),
        sa.CheckConstraint(
            "main_article IS NULL OR main_article <> article", name="ck_wb_reviews_group_overrides_self"
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("product_group_overrides", schema=SCHEMA)
