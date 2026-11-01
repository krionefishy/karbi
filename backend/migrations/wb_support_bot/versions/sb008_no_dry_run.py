from alembic import op

revision = "sb008"
down_revision = "sb007"
branch_labels = None
depends_on = None

SCHEMA = "wb_support_bot"
CONSTRAINT = "ck_wb_support_bot_tracked_mode"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA)
    op.execute(f"UPDATE {SCHEMA}.tracked_sellers SET mode = 'stopped' WHERE mode = 'dry_run'")
    op.alter_column("tracked_sellers", "mode", server_default="stopped", schema=SCHEMA)
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('stopped', 'live')", schema=SCHEMA)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "tracked_sellers", schema=SCHEMA)
    op.execute(f"UPDATE {SCHEMA}.tracked_sellers SET mode = 'dry_run' WHERE mode = 'stopped'")
    op.alter_column("tracked_sellers", "mode", server_default="dry_run", schema=SCHEMA)
    op.create_check_constraint(CONSTRAINT, "tracked_sellers", "mode IN ('dry_run', 'live')", schema=SCHEMA)
