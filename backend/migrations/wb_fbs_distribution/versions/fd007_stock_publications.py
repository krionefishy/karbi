import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fd007"
down_revision = "fd006"
branch_labels = None
depends_on = None

SCHEMA = "wb_fbs_distribution"


def upgrade() -> None:
    # «Последний план» до сих пор определялся по времени создания. Двух расчётов
    # в одну отметку хватает, чтобы порядок стал произвольным, а публиковать
    # произвольный из двух планов нельзя.
    op.add_column(
        "allocation_plans",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        schema=SCHEMA,
    )
    op.create_unique_constraint("uq_wb_fbs_plan_sequence", "allocation_plans", ["sequence"], schema=SCHEMA)

    op.create_table(
        "published_stocks",
        sa.Column("seller_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("warehouse_id", sa.BigInteger(), primary_key=True),
        sa.Column("sku", sa.String(64), primary_key=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        "stock_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("seller_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=True),
        sa.Column("warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("drift", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('verified', 'drift', 'failed')", name="ck_wb_fbs_publication_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_wb_fbs_publications_seller", "stock_publications", ["seller_id", "created_at"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_table("stock_publications", schema=SCHEMA)
    op.drop_table("published_stocks", schema=SCHEMA)
    op.drop_constraint("uq_wb_fbs_plan_sequence", "allocation_plans", schema=SCHEMA)
    op.drop_column("allocation_plans", "sequence", schema=SCHEMA)
