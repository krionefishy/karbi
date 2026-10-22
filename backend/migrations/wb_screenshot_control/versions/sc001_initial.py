import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BIT, CHAR, JSONB, UUID

revision = "sc001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "wb_screenshot_control"

VERDICTS = "'accepted', 'duplicate', 'needs_review', 'rejected'"


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", CHAR(64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("mime", sa.String(16), nullable=False),
        sa.Column("width", sa.SmallInteger(), nullable=False),
        sa.Column("height", sa.SmallInteger(), nullable=False),
        sa.Column("dhash", BIT(64), nullable=True),
        sa.Column("tile_hashes", sa.LargeBinary(), nullable=True),
        sa.Column("meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pinned_by", UUID(as_uuid=True), nullable=True),
        sa.Column("pinned_note", sa.String(500), nullable=True),
        _created_at(),
        sa.CheckConstraint("size_bytes > 0", name="ck_sc_files_size"),
        sa.CheckConstraint("mime IN ('image/png', 'image/jpeg', 'image/webp')", name="ck_sc_files_mime"),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_files_created", "files", ["created_at"], schema=SCHEMA)

    op.create_table(
        "submissions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("client_submission_id", sa.String(80), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.files.id"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("install_id", UUID(as_uuid=True), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_media_group_id", sa.String(64), nullable=True),
        sa.Column("client_url", sa.String(2048), nullable=True),
        sa.Column("client_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_tz_offset_min", sa.SmallInteger(), nullable=True),
        sa.Column("client_meta", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("outcome", sa.String(8), nullable=True),
        sa.Column("outcome_source", sa.String(8), nullable=True),
        sa.Column("caption_raw", sa.String(500), nullable=True),
        sa.Column("outcome_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage", sa.String(10), nullable=False, server_default="processing"),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("case_kind", sa.String(8), nullable=True),
        sa.Column("chat_id_norm", CHAR(36), nullable=True),
        sa.Column("chat_id_source", sa.String(8), nullable=True),
        sa.Column("key_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("key_time_precision", sa.String(6), nullable=True),
        sa.Column("text_norm", sa.String(4000), nullable=True),
        sa.Column("text_len", sa.SmallInteger(), nullable=True),
        sa.Column("text_key", CHAR(40), nullable=True),
        sa.Column("buyer_hmac", CHAR(16), nullable=True),
        sa.Column("article_norm", sa.String(32), nullable=True),
        sa.Column("verdict", sa.String(12), nullable=True),
        sa.Column("auto_verdict", sa.String(12), nullable=True),
        sa.Column("reasons", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("duplicate_of", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.submissions.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(1000), nullable=True),
        sa.Column("audit_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("audit_done_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "client_submission_id", name="uq_sc_sub_client_id"),
        sa.CheckConstraint("channel IN ('extension', 'telegram', 'web')", name="ck_sc_sub_channel"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('positive', 'amended', 'deleted')", name="ck_sc_sub_outcome"
        ),
        sa.CheckConstraint(
            "outcome_source IS NULL OR outcome_source IN ('button', 'caption', 'form', 'admin')",
            name="ck_sc_sub_outcome_source",
        ),
        sa.CheckConstraint("stage IN ('processing', 'extracted', 'decided')", name="ck_sc_sub_stage"),
        sa.CheckConstraint("case_kind IS NULL OR case_kind IN ('desktop', 'mobile')", name="ck_sc_sub_case_kind"),
        sa.CheckConstraint(
            "chat_id_source IS NULL OR chat_id_source IN ('url', 'image')", name="ck_sc_sub_chat_source"
        ),
        sa.CheckConstraint(
            "key_time_precision IS NULL OR key_time_precision IN ('minute', 'day')", name="ck_sc_sub_precision"
        ),
        sa.CheckConstraint(f"verdict IS NULL OR verdict IN ({VERDICTS})", name="ck_sc_sub_verdict"),
        sa.CheckConstraint(f"auto_verdict IS NULL OR auto_verdict IN ({VERDICTS})", name="ck_sc_sub_auto_verdict"),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_sub_user_time", "submissions", ["user_id", sa.text("received_at DESC")], schema=SCHEMA)
    op.create_index(
        "ix_sc_sub_stage",
        "submissions",
        ["stage", "received_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("stage <> 'decided'"),
    )
    for name, column in (
        ("ix_sc_sub_chat", "chat_id_norm"),
        ("ix_sc_sub_key_time", "key_time"),
        ("ix_sc_sub_text_key", "text_key"),
    ):
        op.create_index(name, "submissions", [column], schema=SCHEMA, postgresql_where=sa.text(f"{column} IS NOT NULL"))
    op.create_index("ix_sc_sub_file", "submissions", ["file_id"], schema=SCHEMA)
    op.create_index(
        "ix_sc_sub_review",
        "submissions",
        ["received_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("verdict = 'needs_review'"),
    )
    op.create_index(
        "ix_sc_sub_audit",
        "submissions",
        ["received_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("audit_requested AND audit_done_at IS NULL"),
    )
    op.create_index(
        "ix_sc_sub_kpi",
        "submissions",
        ["user_id", "received_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("verdict = 'accepted'"),
    )

    op.create_table(
        "extractions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.files.id"), nullable=False),
        sa.Column("pipeline_version", sa.String(16), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("layout", sa.String(24), nullable=True),
        sa.Column("vlm_model", sa.String(128), nullable=True),
        sa.Column("vlm_prompt", sa.String(16), nullable=True),
        sa.Column("vlm_json", JSONB(), nullable=True),
        sa.Column("ocr_json", JSONB(), nullable=True),
        sa.Column("fields", JSONB(), nullable=True),
        sa.Column("agreement", JSONB(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        _created_at(),
        sa.UniqueConstraint("file_id", "pipeline_version", name="uq_sc_extractions_file_version"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_sc_extractions_status"),
        schema=SCHEMA,
    )

    op.create_table(
        "verdict_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("submission_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.submissions.id"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor", sa.String(48), nullable=False),
        sa.Column("from_verdict", sa.String(12), nullable=True),
        sa.Column("to_verdict", sa.String(12), nullable=True),
        sa.Column("reasons", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("note", sa.String(1000), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_events_sub", "verdict_events", ["submission_id", "at"], schema=SCHEMA)

    op.create_table(
        "storage_objects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey(f"{SCHEMA}.files.id"), nullable=False),
        sa.Column("kind", sa.String(5), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("s3_key", sa.String(200), nullable=False),
        sa.Column("staging_path", sa.String(200), nullable=True),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", CHAR(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("file_id", "kind", name="uq_sc_objects_file_kind"),
        sa.UniqueConstraint("bucket", "s3_key", name="uq_sc_objects_key"),
        sa.CheckConstraint("kind IN ('orig', 'thumb')", name="ck_sc_objects_kind"),
        sa.CheckConstraint(
            "state IN ('staged', 'uploading', 'stored', 'delete_requested', 'deleted', 'failed')",
            name="ck_sc_objects_state",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_sc_objects_size"),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_objects_state", "storage_objects", ["state", "updated_at"], schema=SCHEMA)
    op.create_index("ix_sc_objects_stored", "storage_objects", ["stored_at"], schema=SCHEMA)

    op.create_table(
        "extension_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("install_id", sa.String(64), nullable=False),
        sa.Column("browser", sa.String(255), nullable=False, server_default=""),
        sa.Column("token_hash", CHAR(64), nullable=False, unique=True),
        sa.Column("ext_version", sa.String(16), nullable=True),
        _created_at(),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_installs_user", "extension_installs", ["user_id", "revoked_at"], schema=SCHEMA)

    op.create_table(
        "pairing_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("code", CHAR(8), nullable=False),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("install_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_sc_pairing_live",
        "pairing_codes",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("used_at IS NULL"),
    )
    op.create_index("ix_sc_pairing_user", "pairing_codes", ["user_id", "expires_at"], schema=SCHEMA)

    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", CHAR(64), nullable=False, unique=True),
        _created_at(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index("ix_sc_link_tokens_user", "telegram_link_tokens", ["user_id", "expires_at"], schema=SCHEMA)

    op.create_table(
        "telegram_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(255), nullable=False, server_default=""),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_sc_tg_user_live",
        "telegram_links",
        ["telegram_user_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_sc_tg_staff_live",
        "telegram_links",
        ["user_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    for table in (
        "telegram_links",
        "telegram_link_tokens",
        "pairing_codes",
        "extension_installs",
        "storage_objects",
        "verdict_events",
        "extractions",
        "submissions",
        "files",
    ):
        op.drop_table(table, schema=SCHEMA)
