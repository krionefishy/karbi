"""Отозвать неработающие установки, после которых кабинет подключил расширение заново.

До этой правки новая пара заменяла только установку с тем же `install_id`; брошенные
попытки оставались в `needs_login` и каждый день слали тревогу рядом с исправной установкой.
"""

from alembic import op

revision = "rt003"
down_revision = "rt002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE wb_returns.extension_installs AS stale
        SET revoked_at = now()
        WHERE stale.revoked_at IS NULL
          AND stale.state <> 'ok'
          AND EXISTS (
              SELECT 1 FROM wb_returns.extension_installs AS newer
              WHERE newer.seller_id = stale.seller_id
                AND newer.revoked_at IS NULL
                AND newer.created_at > stale.created_at
          )
        """
    )


def downgrade() -> None:
    # Отзыв не откатывается: какие установки были живыми до него, уже не узнать.
    pass
