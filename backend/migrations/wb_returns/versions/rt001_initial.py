"""Возвраты WB: подключённые кабинеты, возвраты по стикерам, заявки покупателей,
журнал уведомлений и запросы «Обновить».

Возврат хранится под стикером WB (`shk_id`): отчёт за окно перечитывается
каждый проход, и повтор той же строки только обновляет статус.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "rt001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_returns"


def upgrade() -> None:
    op.create_table(
        "tracked_sellers",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collection_error", sa.String(), nullable=True),
        sa.Column("claims_archived_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "returns",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("shk_id", sa.BigInteger(), primary_key=True),
        sa.Column("sticker_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("srid", sa.String(64), nullable=False, server_default=""),
        sa.Column("order_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("nm_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("barcode", sa.String(64), nullable=False, server_default=""),
        sa.Column("brand", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("tech_size", sa.String(32), nullable=False, server_default=""),
        sa.Column("return_type", sa.String(255), nullable=False, server_default=""),
        sa.Column("reason", sa.String(512), nullable=False, server_default=""),
        sa.Column("status", sa.String(64), nullable=False, server_default=""),
        sa.Column("status_key", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dst_office_id", sa.BigInteger(), nullable=True),
        sa.Column("dst_office_address", sa.String(512), nullable=False, server_default=""),
        sa.Column("order_dt", sa.Date(), nullable=True),
        sa.Column("ready_to_return_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_returns_returns_active", "returns", ["seller_id", "is_active", "status_key"], schema=SCHEMA)
    op.create_index("ix_wb_returns_returns_changed", "returns", ["seller_id", "status_changed_at"], schema=SCHEMA)
    op.create_table(
        "claims",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("claim_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("claim_type", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_ex", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nm_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("imt_name", sa.String(512), nullable=False, server_default=""),
        sa.Column("user_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("wb_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("dt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("order_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dt_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_dt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("currency_code", sa.String(8), nullable=False, server_default=""),
        sa.Column("srid", sa.String(64), nullable=False, server_default=""),
        sa.Column("photos", JSONB(), nullable=False, server_default="[]"),
        sa.Column("video_paths", JSONB(), nullable=False, server_default="[]"),
        sa.Column("actions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("is_archive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_returns_claims_open", "claims", ["seller_id", "is_archive", "status", "dt"], schema=SCHEMA)
    op.create_table(
        "notification_log",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "refresh_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'running', 'success', 'error')", name="ck_wb_returns_refresh_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_returns_refresh_seller", "refresh_requests", ["seller_id", "requested_at"], schema=SCHEMA)
    op.create_index(
        "uq_wb_returns_refresh_active",
        "refresh_requests",
        ["seller_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    for table in ("refresh_requests", "notification_log", "claims", "returns", "tracked_sellers"):
        op.drop_table(table, schema=SCHEMA)
