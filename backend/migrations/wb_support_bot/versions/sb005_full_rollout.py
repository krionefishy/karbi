import sqlalchemy as sa
from alembic import op

revision = "sb005"
down_revision = "sb004"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"
CONSTRAINT = "ck_wb_support_bot_tracked_mode"


def upgrade() -> None:
    # Кабинеты с долевой отправкой переходят на отправку по всем диалогам; момент перехода — метка на графике ответов.
    op.execute(
        f"INSERT INTO {SCHEMA}.mode_changes (seller_id, mode, changed_at) "
        f"SELECT seller_id, 'live', now() FROM {SCHEMA}.tracked_sellers WHERE mode = 'canary'"
    )
    op.execute(f"UPDATE {SCHEMA}.tracked_sellers SET mode = 'live' WHERE mode = 'canary'")
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA, type_="check")
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('dry_run', 'live')", schema=SCHEMA)
    op.drop_column("mode_changes", "canary_percent", schema=SCHEMA)


def downgrade() -> None:
    op.add_column("mode_changes", sa.Column("canary_percent", sa.Integer(), nullable=True), schema=SCHEMA)
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA, type_="check")
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('dry_run', 'canary', 'live')", schema=SCHEMA)
