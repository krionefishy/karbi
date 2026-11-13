"""Read-path indexes for the ma-bridge export cursor."""

from alembic import op

revision = "cc004"
down_revision = "cc003"
branch_labels = None
depends_on = None

SCHEMA = "wb_card_checklist"
INDEXES = (
    ("ix_wb_card_checklist_card_facts_export", "card_facts", ["collected_at", "seller_id", "article"]),
    ("ix_wb_card_checklist_price_facts_export", "price_facts", ["collected_at", "seller_id", "article"]),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in INDEXES:
            op.create_index(name, table, columns, schema=SCHEMA, postgresql_concurrently=True)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, _ in reversed(INDEXES):
            op.drop_index(name, table_name=table, schema=SCHEMA, postgresql_concurrently=True)
