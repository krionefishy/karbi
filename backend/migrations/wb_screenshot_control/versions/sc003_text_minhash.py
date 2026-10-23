"""MinHash текста у сабмишена: ключ кандидатов без чата и времени (подмена chatId вместе с правкой).

Колонка заполняется для уже распознанных скринов по их `text_norm` той же функцией, что в конвейере.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

from backend.modules.wb_screenshot_control.domain.similarity import text_minhash

revision = "sc003"
down_revision = "sc002"
branch_labels = None
depends_on = None

SCHEMA = "wb_screenshot_control"
BATCH = 500


def upgrade() -> None:
    op.add_column("submissions", sa.Column("text_minhash", ARRAY(sa.BigInteger()), nullable=True), schema=SCHEMA)
    connection = op.get_bind()
    select = sa.text(
        f"SELECT id, text_norm FROM {SCHEMA}.submissions "
        "WHERE text_norm IS NOT NULL AND text_minhash IS NULL AND id > :after ORDER BY id LIMIT :limit"
    )
    update = sa.text(f"UPDATE {SCHEMA}.submissions SET text_minhash = :minhash WHERE id = :id").bindparams(
        sa.bindparam("minhash", type_=ARRAY(sa.BigInteger()))
    )
    after = "00000000-0000-0000-0000-000000000000"
    while True:
        rows = connection.execute(select, {"after": after, "limit": BATCH}).all()
        for row in rows:
            minhash = text_minhash(row.text_norm)
            if minhash is not None:
                connection.execute(update, {"id": row.id, "minhash": list(minhash)})
        if len(rows) < BATCH:
            break
        after = rows[-1].id
    op.create_index(
        "ix_sc_sub_text_minhash",
        "submissions",
        ["text_minhash"],
        schema=SCHEMA,
        postgresql_using="gin",
        postgresql_where=sa.text("text_minhash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_sc_sub_text_minhash", table_name="submissions", schema=SCHEMA)
    op.drop_column("submissions", "text_minhash", schema=SCHEMA)
