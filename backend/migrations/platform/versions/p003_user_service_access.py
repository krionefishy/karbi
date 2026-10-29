import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "p003"
down_revision = "p002"
branch_labels = None
depends_on = None

# Список зафиксирован на момент миграции: backfill не должен зависеть от того, что в каталоге сегодня.
CURRENT_SERVICES = (
    "wb-reviews",
    "wb-turnover",
    "wb-card-checklist",
    "wb-card-documents",
    "wb-fbs-stocks",
    "wb-fbs-penalties",
    "wb-review-chats",
    "wb-support-bot",
    "wb-returns",
    "wb-podsort",
    "fin-reports",
    "wb-box-stickers",
)


def upgrade() -> None:
    op.create_table(
        "user_service_access",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_id", sa.String(64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id", "service_id"),
        schema="platform",
    )
    # Все, кто уже работает, сохраняют доступ ко всему: убирать лишнее будет администратор.
    values = ", ".join(f"('{service}')" for service in CURRENT_SERVICES)
    op.execute(
        "INSERT INTO platform.user_service_access (user_id, service_id) "
        f"SELECT u.id, s.id FROM platform.users u CROSS JOIN (VALUES {values}) AS s(id)"
    )


def downgrade() -> None:
    op.drop_table("user_service_access", schema="platform")
