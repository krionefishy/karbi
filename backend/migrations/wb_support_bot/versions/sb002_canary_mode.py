from alembic import op

revision = "sb002"
down_revision = "sb001"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"
CONSTRAINT = "ck_wb_support_bot_tracked_mode"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA, type_="check")
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('dry_run', 'canary', 'live')", schema=SCHEMA)


def downgrade() -> None:
    op.execute(f"UPDATE {SCHEMA}.tracked_sellers SET mode = 'dry_run' WHERE mode = 'canary'")
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA, type_="check")
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('dry_run', 'live')", schema=SCHEMA)
